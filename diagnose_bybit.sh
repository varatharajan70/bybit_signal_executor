#!/bin/bash
# diagnose_bybit.sh - Test ByBit API connection with detailed diagnostics

cd "$(dirname "$0")"

echo "=========================================="
echo "  ByBit API Connection Diagnostics"
echo "=========================================="
echo ""

python3 << 'PYEOF'
import requests
import hmac
import hashlib
import time
import json
from config import BYBIT_API_KEY, BYBIT_API_SECRET, BYBIT_URL

print(f"[CONFIG]")
print(f"  Mode: {'DEMO' if 'testnet' in BYBIT_URL else 'LIVE'}")
print(f"  URL: {BYBIT_URL}")
print(f"  API Key: {BYBIT_API_KEY[:10]}...")
print(f"  API Secret: {'*' * 20}")
print("")

# Test 1: Basic connectivity
print(f"[TEST 1] Basic connectivity")
try:
    resp = requests.get(f"{BYBIT_URL}/v5/market/tickers?category=linear&symbol=BTCUSDT", timeout=5)
    print(f"  Status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        print(f"  ✓ Public API accessible")
        print(f"  ✓ BTC Price: ${data['result']['list'][0]['lastPrice']}")
    else:
        print(f"  ✗ API returned: {resp.status_code}")
except Exception as e:
    print(f"  ✗ Error: {e}")

print("")
print(f"[TEST 2] Authenticated request (wallet balance)")

# Create signature
timestamp = str(int(time.time() * 1000))
query_string = "accountType=UNIFIED"
sign_string = timestamp + BYBIT_API_KEY + query_string
signature = hmac.new(
    BYBIT_API_SECRET.encode(),
    sign_string.encode(),
    hashlib.sha256
).hexdigest()

headers = {
    "X-BAPI-SIGN": signature,
    "X-BAPI-API-KEY": BYBIT_API_KEY,
    "X-BAPI-TIMESTAMP": timestamp,
    "X-BAPI-RECV-WINDOW": "5000",
    "Content-Type": "application/json"
}

try:
    resp = requests.get(
        f"{BYBIT_URL}/v5/account/wallet-balance",
        params={"accountType": "UNIFIED"},
        headers=headers,
        timeout=5
    )
    print(f"  Status: {resp.status_code}")
    data = resp.json()

    if resp.status_code == 200 and data.get("retCode") == 0:
        print(f"  ✓ Authentication successful!")
        balance = data.get("result", {}).get("list", [{}])[0]
        print(f"  ✓ Account type: UNIFIED")
        print(f"  ✓ Coins: {balance.get('coin', [])}")
    elif resp.status_code == 401:
        print(f"  ✗ 401 Unauthorized")
        print(f"    Possible causes:")
        print(f"      1. API key/secret incorrect")
        print(f"      2. IP address not whitelisted (DISABLE IP RESTRICTION)")
        print(f"      3. API key permissions missing (need Trade)")
        print(f"      4. API key expired/disabled")
    else:
        print(f"  ✗ Error {resp.status_code}: {data.get('retMsg')}")
        print(f"  Response: {json.dumps(data, indent=2)}")
except Exception as e:
    print(f"  ✗ Connection error: {e}")
    print(f"    Check: Network, firewall, VPN")

print("")
print("=========================================="
print("  FIX CHECKLIST:")
print("=========================================="
print("  [ ] Go to bybitglobal.com/user/api-management")
print("  [ ] Click API key → Edit")
print("  [ ] Find 'IP Restriction' or 'Bind IP'")
print("  [ ] Set to 'No Restriction' or leave empty")
print("  [ ] Save changes (may take 1-2 min)")
print("  [ ] Re-run: python3 diagnose_bybit.sh")
print("  [ ] If still failing, check Account/Trade permissions")
print("=========================================="

PYEOF
