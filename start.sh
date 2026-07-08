#!/usr/bin/env bash
# Start the bot in the background (Termux or Linux/WSL). Survives closing the
# terminal app - use tail.sh to watch it and stop.sh to stop it.
set -e
cd "$(dirname "$0")"

MODE="${1:-both}"

if [ -f bot.pid ] && kill -0 "$(cat bot.pid)" 2>/dev/null; then
    echo "Bot already running (PID $(cat bot.pid))"
    exit 0
fi

mkdir -p logs

PYTHON=python3
if [ -x .venv/bin/python ]; then
    PYTHON=.venv/bin/python
fi

LOG_FILE="logs/run_$(date +%Y%m%d_%H%M%S).log"
nohup "$PYTHON" -u main.py --mode "$MODE" > "$LOG_FILE" 2>&1 &
PID=$!
echo $PID > bot.pid
sleep 2

if kill -0 "$PID" 2>/dev/null; then
    echo "Bot started (PID $PID, mode=$MODE)"
    echo "Log file: $LOG_FILE"
    echo "Watch logs with: ./tail.sh"
else
    echo "Bot failed to start! Last output:"
    tail -n 40 "$LOG_FILE"
    rm -f bot.pid
    exit 1
fi
