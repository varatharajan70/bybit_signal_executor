#!/usr/bin/env bash
# Live-tail the most recent run log
cd "$(dirname "$0")"

LATEST=$(ls -t logs/run_*.log 2>/dev/null | head -1)
if [ -z "$LATEST" ]; then
    echo "No log file yet - has the bot been started with ./start.sh?"
    exit 0
fi
echo "Tailing $LATEST (Ctrl+C to stop watching, bot keeps running)"
tail -f "$LATEST"
