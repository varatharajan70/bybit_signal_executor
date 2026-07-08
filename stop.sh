#!/usr/bin/env bash
# Stop the background bot started by start.sh
cd "$(dirname "$0")"

if [ ! -f bot.pid ]; then
    echo "No bot.pid found - bot doesn't seem to be running"
    exit 0
fi

PID=$(cat bot.pid)
if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    echo "Bot stopped (PID $PID)"
else
    echo "Bot was not running"
fi
rm -f bot.pid
