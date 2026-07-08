#!/bin/bash
# test_signal.sh - Send test signals to executor

cd "$(dirname "$0")"

echo "[TEST] Sending LONG signal for BTC..."
cat > signal_input.txt << 'EOF'
{
  "symbol": "BTCUSDT",
  "side": "Buy",
  "entry": 45000,
  "stop": 44500,
  "tp": 46000
}
EOF

echo "Signal written to signal_input.txt"
echo ""
echo "To run executor:"
echo "  python3 executor.py"
