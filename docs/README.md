# ByBit Signal Executor

**Purpose:** Receive trading signals from a private channel and execute trades on ByBit with trailing stop.

**Features:**
- ✓ Demo mode (ByBit testnet)
- ✓ Receive signals via file input (manual or webhook)
- ✓ Automatic position sizing ($2.5 fixed risk)
- ✓ Trailing stop support
- ✓ Real-time monitoring

## Setup

### 1. Get ByBit API Keys (Demo/Testnet)

1. Go to https://www.bybitglobal.com/en/trade/usdt/BTCUSDT
2. Account → API (top right)
3. Create New Key (Testnet)
4. Permissions: Trade (write), Position (read)
5. Copy API Key and Secret

### 2. Configure

Edit `config.py`:
```python
DEMO_MODE = True  # False for live trading
BYBIT_API_KEY = "your_testnet_api_key"
BYBIT_API_SECRET = "your_testnet_api_secret"
RISK_USD = 2.5  # Risk per trade
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Run

```bash
python3 executor.py
```

## Sending Signals

Executor listens to `signal_input.txt`. Write signals in JSON or key:value format.

**Format 1: JSON**
```json
{
  "symbol": "BTCUSDT",
  "side": "Buy",
  "entry": 45000,
  "stop": 44500,
  "tp": 46000
}
```

**Format 2: Key:Value**
```
symbol: BTCUSDT
side: Buy
entry: 45000
stop: 44500
tp: 46000
```

**Send Signal (CLI):**
```bash
cat > signal_input.txt << 'EOF'
{"symbol": "BTCUSDT", "side": "Buy", "entry": 45000, "stop": 44500, "tp": 46000}
EOF
```

Executor will:
1. ✓ Parse signal
2. ✓ Validate (prices, percentages)
3. ✓ Calculate position size ($2.5 / stop distance)
4. ✓ Place limit order with TP/SL
5. ✓ Set trailing stop
6. ✓ Send notification
7. ✓ Monitor position

## Demo vs Live

| Mode | URL | Risk |
|---|---|---|
| **DEMO** | testnet.bybit.com | Safe, paper trading |
| **LIVE** | api.bybit.com | Real money, use carefully |

To go live, change `DEMO_MODE = False` in `config.py`.

## Monitoring

Check logs in console for:
- Signal received
- Validation result
- Order placement
- Trailing stop confirmation
- Position updates

## Error Handling

- Invalid signal format → Skipped
- Invalid prices → Rejected
- Already in symbol → Skipped
- API error → Logged

---

**Status:** Ready for demo testing
