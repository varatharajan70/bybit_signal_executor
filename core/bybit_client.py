# core/bybit_client.py
# ByBit API wrapper for demo trading with trailing stop

import requests
import time
import hmac
import hashlib
import json
from decimal import Decimal

from config import settings
from config.settings import (
    BYBIT_URL,
    DEMO_MODE,
    LEVERAGE,
    LEVERAGE_SAFETY_FACTOR,
    MAX_LEVERAGE_CEILING,
    TRADING_ENV,
)
from core.colors import red, green, yellow


class ByBitClient:
    def __init__(self):
        # settings._active_key/_active_secret resolve to demo or live credentials depending on
        # TRADING_ENV, already gated by the live-confirmation check in config/settings.py.
        self.api_key = settings._active_key
        self.api_secret = settings._active_secret
        self.base_url = BYBIT_URL
        self.session = requests.Session()
        self.demo_mode = DEMO_MODE
        self.trading_env = TRADING_ENV
        # Offset (ms) added to local time to approximate server time. Refreshed periodically
        # so a week-long unattended run stays correct even if the host clock drifts - ByBit
        # rejects requests outside recv_window (10002) if local/server time diverges too far.
        self._server_time_offset_ms = 0
        self._server_time_synced_at = 0

    def _get_signature(self, payload):
        """Generate HMAC SHA256 signature for ByBit API."""
        return hmac.new(
            self.api_secret.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()

    def _sync_server_time(self):
        """Refresh the local-to-server clock offset. Cheap, unauthenticated endpoint - safe
        to call frequently. Re-synced every 5 minutes during normal operation, and immediately
        on a 10002 timestamp error so a single sync doesn't have to hold for a full week."""
        try:
            resp = self.session.get(f"{self.base_url}/v5/market/time", timeout=10)
            if resp.status_code == 200:
                server_ms = int(resp.json().get("result", {}).get("timeNano", 0)) // 1_000_000
                if server_ms:
                    local_ms = int(time.time() * 1000)
                    self._server_time_offset_ms = server_ms - local_ms
                    self._server_time_synced_at = local_ms
        except Exception:
            pass  # keep using the last known offset (or 0) rather than crash

    def _now_ms(self):
        """Local time adjusted by the last known offset to server time. Re-syncs
        automatically every 5 minutes so drift doesn't accumulate over a multi-day run."""
        local_ms = int(time.time() * 1000)
        if local_ms - self._server_time_synced_at > 5 * 60 * 1000:
            self._sync_server_time()
        return local_ms + self._server_time_offset_ms

    def _request(self, method, endpoint, params=None, data=None, _retry_on_time_error=True):
        """Make authenticated request to ByBit API."""
        try:
            url = f"{self.base_url}{endpoint}"
            timestamp = str(self._now_ms())
            recv_window = "5000"

            if method == "GET":
                query_string = "&".join([f"{k}={v}" for k, v in (params or {}).items()])
                sign_string = timestamp + self.api_key + recv_window + query_string
                sign = self._get_signature(sign_string)
                headers = {
                    "X-BAPI-SIGN": sign,
                    "X-BAPI-API-KEY": self.api_key,
                    "X-BAPI-TIMESTAMP": timestamp,
                    "X-BAPI-RECV-WINDOW": recv_window,
                    "Content-Type": "application/json"
                }
                resp = self.session.get(url, params=params, headers=headers, timeout=10)
            else:  # POST
                body = json.dumps(data or {})
                sign_string = timestamp + self.api_key + recv_window + body
                sign = self._get_signature(sign_string)
                headers = {
                    "X-BAPI-SIGN": sign,
                    "X-BAPI-API-KEY": self.api_key,
                    "X-BAPI-TIMESTAMP": timestamp,
                    "X-BAPI-RECV-WINDOW": recv_window,
                    "Content-Type": "application/json"
                }
                resp = self.session.post(url, data=body, headers=headers, timeout=10)

            result = resp.json() if resp.status_code == 200 else {"retCode": resp.status_code, "retMsg": resp.text}

            # 10002 = timestamp/recv_window error. Force an immediate re-sync and retry once -
            # this is the failure mode a week-long unattended run is most likely to hit if the
            # host clock drifts, and it shouldn't cost a lost trade signal.
            if result.get("retCode") == 10002 and _retry_on_time_error:
                self._sync_server_time()
                return self._request(method, endpoint, params=params, data=data, _retry_on_time_error=False)

            return result
        except Exception as e:
            return {"retCode": -1, "retMsg": str(e)}

    def get_instrument_info(self, symbol):
        """Fetch qty step, min qty, tick size, and max leverage for a symbol."""
        params = {"category": "linear", "symbol": symbol}
        result = self._request("GET", "/v5/market/instruments-info", params=params)
        if result.get("retCode") == 0:
            items = result.get("result", {}).get("list", [])
            if items:
                lot = items[0].get("lotSizeFilter", {})
                price_filter = items[0].get("priceFilter", {})
                leverage_filter = items[0].get("leverageFilter", {})
                return {
                    "minOrderQty": float(lot.get("minOrderQty", 0.001)),
                    "qtyStep": float(lot.get("qtyStep", 0.001)),
                    "tickSize": float(price_filter.get("tickSize", 0.0001)),
                    "maxLeverage": float(leverage_filter.get("maxLeverage", 0)) or None,
                }
        return None

    def get_maintenance_margin_rate(self, symbol):
        """Lowest-tier (smallest position size) maintenance margin rate for a symbol, used
        to estimate how close ByBit's liquidation price sits to the entry at a given
        leverage. Returns None if it can't be determined - callers should fall back to a
        conservative estimate rather than assume 0."""
        result = self._request("GET", "/v5/market/risk-limit", params={"category": "linear", "symbol": symbol})
        if result.get("retCode") == 0:
            items = result.get("result", {}).get("list", [])
            if items:
                return float(items[0].get("maintenanceMargin", 0)) or None
        return None

    def calc_safe_leverage(self, symbol, stop_pct):
        """Pick the highest leverage such that ByBit's liquidation price still stays at
        least LEVERAGE_SAFETY_FACTOR times further from entry than this trade's own
        stop-loss distance (stop_pct) - so the strategy's stop always fires before the
        exchange would force-liquidate, with headroom for slippage/fees. Falls back to the
        fixed LEVERAGE setting if instrument/risk-limit data can't be fetched.

        Liquidation distance for isolated margin is approximately (1/leverage - maintenance
        margin rate) - solving that for the max leverage that keeps liquidation distance >=
        LEVERAGE_SAFETY_FACTOR * stop_pct gives:
            leverage <= 1 / (LEVERAGE_SAFETY_FACTOR * stop_pct + maintenance_margin_rate)
        """
        if self.demo_mode:
            return LEVERAGE
        info = self.get_instrument_info(symbol)
        mmr = self.get_maintenance_margin_rate(symbol)
        if not info or mmr is None or stop_pct <= 0:
            return LEVERAGE

        raw_leverage = 1.0 / (LEVERAGE_SAFETY_FACTOR * stop_pct + mmr)
        symbol_max = info.get("maxLeverage") or LEVERAGE
        leverage = min(raw_leverage, symbol_max, MAX_LEVERAGE_CEILING)
        leverage = max(leverage, 1.0)
        return round(leverage, 2)

    def _round_to_step(self, value, step):
        """Round value to the nearest valid step (qtyStep/tickSize from the exchange).

        step often arrives as a small float like 1e-05 (very common tick size for low-price
        scalping symbols: DOGE, XRP, ADA, etc). str(1e-05) == "1e-05" has no "." in it, so a
        naive `"." in str(step)` precision check silently falls back to 0 decimal places and
        rounds the price to 0 - previously caused every DOGE/XRP-class order to be rejected
        with "Price invalid". Use Decimal so both plain-decimal and scientific-notation steps
        get their precision correctly.
        """
        if step <= 0:
            return value
        step_dec = Decimal(str(step))
        precision = max(0, -step_dec.as_tuple().exponent)
        rounded = round(value / step) * step
        return round(rounded, precision)

    def place_order(self, symbol, side, qty, price, stop_price=None, tp_price=None):
        """
        Place limit order with optional TP/SL.
        side: "Buy" or "Sell"
        qty: quantity in contracts
        price: entry price (limit)
        stop_price: stop loss price
        tp_price: take profit price
        """
        # Demo mode: simulate order (no network calls, no funds at risk)
        if self.demo_mode:
            import random
            order_id = f"DEMO_{random.randint(100000, 999999)}"
            return {
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "orderId": order_id,
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "price": price,
                    "stopLoss": stop_price,
                    "takeProfit": tp_price,
                    "msg": "Demo order simulated"
                }
            }

        # Fetch symbol-specific qty step and round correctly
        info = self.get_instrument_info(symbol)
        if info:
            qty = self._round_to_step(float(qty), info["qtyStep"])
            qty = max(qty, info["minOrderQty"])
            price = self._round_to_step(float(price), info["tickSize"])
            if stop_price:
                stop_price = self._round_to_step(float(stop_price), info["tickSize"])
            if tp_price:
                tp_price = self._round_to_step(float(tp_price), info["tickSize"])
        else:
            qty = max(1.0, round(float(qty), 2))

        data = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": "Limit",
            "qty": str(qty),
            "price": str(price),
            # GTC, not PostOnly: still a limit order (never fills worse than `price`), but
            # won't be hard-rejected if the market has already reached/passed the entry by
            # the time this posts - it just fills immediately instead. Signal validation
            # already assumes worst-case taker fees on both entry and exit (TP/SL legs are
            # plain GTC limits too, so they can already take liquidity), so this doesn't
            # change the profitability math - it only stops fast-moving signals from being
            # rejected outright with no position opened at all.
            "timeInForce": "GTC",
        }

        if stop_price:
            data["stopLoss"] = str(stop_price)
        if tp_price:
            data["takeProfit"] = str(tp_price)

        result = self._request("POST", "/v5/order/create", data=data)
        return result

    def place_reduce_only_limit(self, symbol, side, qty, price):
        """Place a reduce-only limit order - used for a single TP leg of a multi-target
        exit. `side` here is the CLOSING side (opposite of the position's side), matching
        the convention used by close_position."""
        if self.demo_mode:
            import random
            return {
                "retCode": 0,
                "retMsg": "OK",
                "result": {"orderId": f"DEMO_TP_{random.randint(100000, 999999)}"},
            }

        info = self.get_instrument_info(symbol)
        if info:
            qty = self._round_to_step(float(qty), info["qtyStep"])
            qty = max(qty, info["minOrderQty"])
            price = self._round_to_step(float(price), info["tickSize"])
        else:
            qty = max(1.0, round(float(qty), 2))

        data = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": "Limit",
            "qty": str(qty),
            "price": str(price),
            "timeInForce": "GTC",
            "reduceOnly": True,
        }
        return self._request("POST", "/v5/order/create", data=data)

    def get_order_status(self, symbol, order_id):
        """Returns the orderStatus string (e.g. "Filled", "New", "Cancelled") for a
        specific order, or None if it couldn't be determined."""
        if self.demo_mode:
            return None

        params = {"category": "linear", "symbol": symbol, "orderId": order_id}
        result = self._request("GET", "/v5/order/realtime", params=params)
        if result.get("retCode") == 0:
            items = result.get("result", {}).get("list", [])
            if items:
                return items[0].get("orderStatus")
        return None

    def cancel_order(self, symbol, order_id):
        """Cancel a still-open order (used to clean up leftover TP leg orders once a
        position has fully closed via SL or the final TP)."""
        if self.demo_mode:
            return {"retCode": 0, "retMsg": "OK", "result": {}}

        data = {"category": "linear", "symbol": symbol, "orderId": order_id}
        return self._request("POST", "/v5/order/cancel", data=data)

    def set_stop_loss(self, symbol, price):
        """Move the stop-loss for an open position to a new price (e.g. breakeven after
        TP2, or TP2's price after TP3)."""
        if self.demo_mode:
            return {"retCode": 0, "retMsg": "OK", "result": {"msg": "Demo SL move simulated"}}

        info = self.get_instrument_info(symbol)
        tick = info["tickSize"] if info else 0.0001
        data = {
            "category": "linear",
            "symbol": symbol,
            "stopLoss": str(self._round_to_step(float(price), tick)),
        }
        return self._request("POST", "/v5/position/trading-stop", data=data)

    def set_leverage(self, symbol, leverage):
        """Set buy/sell leverage for a symbol (must be called before/after opening a position)."""
        if self.demo_mode:
            return {"retCode": 0, "retMsg": "OK", "result": {"msg": "Demo leverage simulated"}}

        data = {
            "category": "linear",
            "symbol": symbol,
            "buyLeverage": str(leverage),
            "sellLeverage": str(leverage),
        }
        result = self._request("POST", "/v5/position/set-leverage", data=data)
        # 110043 = "leverage not modified" - already set to this value, not a real error
        if result.get("retCode") == 110043:
            return {"retCode": 0, "retMsg": "OK (leverage already set)", "result": {}}
        return result

    def get_ticker(self, symbol):
        """Get the current last traded price for a symbol."""
        params = {"category": "linear", "symbol": symbol}
        result = self._request("GET", "/v5/market/tickers", params=params)
        if result.get("retCode") == 0:
            items = result.get("result", {}).get("list", [])
            if items:
                return float(items[0].get("lastPrice", 0))
        return None

    def get_executions(self, symbol=None, limit=20):
        """Get recent trade executions (fills), including commission/fee info."""
        if self.demo_mode:
            return {"retCode": 0, "retMsg": "OK", "result": {"list": []}}

        params = {"category": "linear", "limit": limit}
        if symbol:
            params["symbol"] = symbol
        result = self._request("GET", "/v5/execution/list", params=params)
        return result

    def place_market_order(self, symbol, side, qty, stop_price=None, tp_price=None):
        """Place a market order (guarantees a fill on demo, unlike PostOnly limit orders)."""
        if self.demo_mode:
            import random
            order_id = f"DEMO_{random.randint(100000, 999999)}"
            return {
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "orderId": order_id,
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "stopLoss": stop_price,
                    "takeProfit": tp_price,
                    "msg": "Demo market order simulated"
                }
            }

        info = self.get_instrument_info(symbol)
        if info:
            qty = self._round_to_step(float(qty), info["qtyStep"])
            qty = max(qty, info["minOrderQty"])
            if stop_price:
                stop_price = self._round_to_step(float(stop_price), info["tickSize"])
            if tp_price:
                tp_price = self._round_to_step(float(tp_price), info["tickSize"])
        else:
            qty = max(1.0, round(float(qty), 2))

        data = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
            "qty": str(qty),
            "timeInForce": "IOC",
        }
        if stop_price:
            data["stopLoss"] = str(stop_price)
        if tp_price:
            data["takeProfit"] = str(tp_price)

        result = self._request("POST", "/v5/order/create", data=data)
        return result

    def set_trailing_stop(self, symbol, side, trailing_amount):
        """Set trailing stop after position is opened. trailing_amount is a price distance
        (not a percent) and must land on the instrument's tick grid or ByBit rejects it -
        this matters most for scalping low-price symbols (DOGE, XRP) where ticks are coarse
        relative to a tight trailing distance."""
        if self.demo_mode:
            return {"retCode": 0, "retMsg": "OK", "result": {"msg": "Demo trailing stop simulated"}}

        info = self.get_instrument_info(symbol)
        if info:
            trailing_amount = self._round_to_step(float(trailing_amount), info["tickSize"])
            # Never let rounding collapse the trailing distance to zero - that would either be
            # rejected or (worse) behave like an immediate stop-at-market.
            if trailing_amount <= 0:
                trailing_amount = info["tickSize"]

        data = {
            "category": "linear",
            "symbol": symbol,
            "trailingStop": str(trailing_amount),
        }
        result = self._request("POST", "/v5/position/trading-stop", data=data)
        return result

    def check_and_fix_protection(self, symbol=None, skip_tp_symbols=None):
        """Reconcile that open positions actually carry a stopLoss (and, for positions not
        managed leg-by-leg, a takeProfit). Placing an order with SL/TP attached and trusting
        the retCode isn't enough - ByBit can accept the order but drop the protection fields
        silently. This re-checks live position state and re-applies a missing stopLoss so a
        position never runs naked.

        skip_tp_symbols: symbols whose take-profit is intentionally handled via separate
        reduce-only TP-leg orders (core/trade_tracker.py) rather than the position's takeProfit
        field. For those, a "missing" takeProfit is expected, not dropped protection - patching
        in a generic default TP here would silently override the real multi-leg TP plan with an
        unrelated price.

        Returns a list of {symbol, side, fixed: [...], errors: [...]} for positions that needed
        a fix; empty list means everything already had protection in place.
        """
        if self.demo_mode:
            return []

        skip_tp_symbols = skip_tp_symbols or set()
        result = self.get_positions(symbol=symbol)
        if result.get("retCode") != 0:
            return []

        fixed = []
        for pos in result.get("result", {}).get("list", []):
            if float(pos.get("size", 0)) <= 0:
                continue

            sym = pos.get("symbol")
            side = pos.get("side")
            avg_price = float(pos.get("avgPrice", 0) or 0)
            has_sl = float(pos.get("stopLoss", 0) or 0) > 0
            has_tp = float(pos.get("takeProfit", 0) or 0) > 0
            tp_managed_by_legs = sym in skip_tp_symbols

            if has_sl and (has_tp or tp_managed_by_legs):
                continue
            if avg_price <= 0:
                continue

            entry = {"symbol": sym, "side": side, "fixed": [], "errors": []}
            info = self.get_instrument_info(sym)
            tick = info["tickSize"] if info else 0.0001

            data = {"category": "linear", "symbol": sym}
            # Reconstruct a missing SL at a conservative default distance (2%) only when it was
            # never set - we do not overwrite an intentionally different SL that the signal set,
            # only a field that's truly missing (0). TP is only reconstructed for positions that
            # aren't already managed via TP-leg orders.
            if not has_sl:
                default_stop_pct = 0.02
                sl = avg_price * (1 - default_stop_pct) if side == "Buy" else avg_price * (1 + default_stop_pct)
                data["stopLoss"] = str(self._round_to_step(sl, tick))
                entry["fixed"].append("stopLoss")
            if not has_tp and not tp_managed_by_legs:
                default_tp_pct = 0.03
                tp = avg_price * (1 + default_tp_pct) if side == "Buy" else avg_price * (1 - default_tp_pct)
                data["takeProfit"] = str(self._round_to_step(tp, tick))
                entry["fixed"].append("takeProfit")

            if entry["fixed"]:
                resp = self._request("POST", "/v5/position/trading-stop", data=data)
                if resp.get("retCode") != 0:
                    entry["errors"].append(resp.get("retMsg"))
                fixed.append(entry)

        return fixed

    def get_positions(self, symbol=None):
        """Get open positions."""
        if self.demo_mode:
            return {"retCode": 0, "retMsg": "OK", "result": {"list": []}}

        params = {"category": "linear", "settleCoin": "USDT"}
        if symbol:
            params["symbol"] = symbol
        result = self._request("GET", "/v5/position/list", params=params)
        return result

    def close_position(self, symbol, side):
        """Close a position with a reduce-only market order."""
        if self.demo_mode:
            return {"retCode": 0, "retMsg": "OK", "result": {"msg": "Demo close simulated"}}

        info = self.get_instrument_info(symbol)
        positions = self.get_positions(symbol=symbol)
        qty = "0"
        if positions.get("retCode") == 0:
            pos_list = positions.get("result", {}).get("list", [])
            if pos_list:
                qty = pos_list[0].get("size", "0")

        data = {
            "category": "linear",
            "symbol": symbol,
            "side": "Sell" if side == "Buy" else "Buy",
            "qty": str(qty),
            "orderType": "Market",
            "timeInForce": "IOC",
            "reduceOnly": True,
        }
        result = self._request("POST", "/v5/order/create", data=data)
        return result

    def get_account_balance(self):
        """Get account balance."""
        if self.demo_mode:
            return {"retCode": 0, "retMsg": "OK", "result": {"msg": "Demo balance simulated"}}
        result = self._request("GET", "/v5/account/wallet-balance", params={"accountType": "UNIFIED"})
        return result


if __name__ == "__main__":
    client = ByBitClient()
    print(yellow("[ByBit Client] Testing API connectivity..."))
    print(yellow(f"  Trading env: {TRADING_ENV.upper()}"))
    print(yellow(f"  Base URL: {BYBIT_URL}"))
    if DEMO_MODE:
        print(green("[OK] Running in DEMO MODE (simulated orders, no network calls, no funds at risk)"))
        print(yellow(f"  API Key: {(client.api_key or '')[:10]}..."))
        print(yellow("  Ready to execute test signals"))
    else:
        if client.trading_env == "live":
            print(red("[WARNING] LIVE trading env - real funds are at risk on any order placed below."))
        balance = client.get_account_balance()
        if balance.get("retCode") == 0:
            print(green(f"[OK] Connected to ByBit {client.trading_env} API: {BYBIT_URL}"))
            print(green(f"  Balance: {balance}"))
        else:
            print(red(f"[FAIL] Connection failed: {balance}"))
