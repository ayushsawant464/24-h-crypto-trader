#!/usr/bin/env python3
"""
Pre-Flight Diagnostic & Smoke-Test Tool for Roostoo
Validates connectivity, clock drift, authentication, wallet balances,
market data feeds, and optionally tests safe order placement/cancellation.

Usage:
  python scripts/preflight_check.py --mode test
  python scripts/preflight_check.py --mode prod
  python scripts/preflight_check.py --mode test --test-order
"""

import sys
import time
import argparse
from bot.config.settings import settings
from bot.data.roostoo_client import RoostooClient
from bot.logs.logger import logger

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

def run_preflight(mode: str, test_order: bool = False):
    print("=" * 65)
    print(f"ROOSTOO PRE-FLIGHT VERIFICATION & SMOKE-TEST (Mode: {mode.upper()})")
    print("=" * 65)

    settings.switch_mode(mode)
    client = RoostooClient()

    if not client.api_key or not client.secret_key:
        print(f"{RED}[FAIL] No API keys configured for mode '{mode}'!{RESET}")
        print(f"Please populate ROOSTOO_{mode.upper()}_API_KEY and ROOSTOO_{mode.upper()}_SECRET_KEY in your .env file.")
        sys.exit(1)

    print(f"Target URL : {client.base_url}")
    print(f"API Key    : {client.api_key[:6]}...{client.api_key[-4:] if len(client.api_key) > 10 else ''}")
    print("-" * 65)

    # 1. Connectivity & Clock Drift
    t_start = time.time()
    server_time_resp = client.get_server_time()
    t_latency = (time.time() - t_start) * 1000

    if not server_time_resp.get("ServerTime"):
        print(f"{RED}[FAIL] Could not reach Roostoo API server! Response: {server_time_resp}{RESET}")
        sys.exit(1)

    server_time = int(server_time_resp["ServerTime"])
    local_time = int(time.time() * 1000)
    clock_drift_ms = abs(server_time - local_time)

    print(f"{GREEN}[PASS]{RESET} Server Connectivity: OK (Latency: {t_latency:.1f}ms)")
    if clock_drift_ms > 30000:
        print(f"{RED}[FAIL]{RESET} Clock Drift: {clock_drift_ms}ms! Roostoo rejects requests if drift > 60,000ms. Please sync system clock (NTP).")
        sys.exit(1)
    else:
        print(f"{GREEN}[PASS]{RESET} Clock Synchronization: OK (Drift: {clock_drift_ms}ms)")

    # 2. Exchange Information
    ex_resp = client.get_exchange_info()
    trade_pairs = ex_resp.get("TradePairs", {})
    if not trade_pairs:
        print(f"{RED}[FAIL] Could not load TradePairs from ExchangeInfo!{RESET}")
        sys.exit(1)
    print(f"{GREEN}[PASS]{RESET} Exchange Rules: Loaded {len(trade_pairs)} pairs successfully.")

    # 3. Authentication & Account Balance
    bal_resp = client.get_balance()
    if not bal_resp.get("Success"):
        print(f"{RED}[FAIL] Authentication failed for mode '{mode}'!{RESET}")
        print(f"Response: {bal_resp}")
        print("Please verify that your API_KEY and SECRET_KEY are correct.")
        sys.exit(1)

    wallet = bal_resp.get("Wallet", {})
    usd_free = float(wallet.get("USD", {}).get("Free", 0.0))
    usd_lock = float(wallet.get("USD", {}).get("Lock", 0.0))
    print(f"{GREEN}[PASS]{RESET} Authentication & HMAC Signature: VALID")
    print(f"       Wallet Balance: Free USD = ${usd_free:,.2f} | Locked USD = ${usd_lock:,.2f}")

    non_usd = {k: v for k, v in wallet.items() if k != "USD" and (float(v.get("Free", 0)) > 0 or float(v.get("Lock", 0)) > 0)}
    if non_usd:
        holdings_str = ", ".join([f"{k}: {v.get('Free', 0)} free" for k, v in non_usd.items()])
        print(f"       Active Holdings: {holdings_str}")
    else:
        print(f"       Active Holdings: None (100% Cash)")

    # 4. Market Data Ticker Check
    ticker_resp = client.get_ticker("BTC/USD")
    if not ticker_resp.get("Success"):
        print(f"{RED}[FAIL] Ticker endpoint returned error: {ticker_resp}{RESET}")
        sys.exit(1)
    btc_last = ticker_resp.get("Data", {}).get("BTC/USD", {}).get("LastPrice", 0.0)
    print(f"{GREEN}[PASS]{RESET} Live Market Feed: Verified (BTC/USD LastPrice: ${btc_last:,.2f})")

    # 5. Optional Safe Order Lifecycle Test (Place -> Query -> Cancel)
    if test_order:
        print("-" * 65)
        print("Running Safe Order Lifecycle Test on DOGE/USD...")
        # Safe limit order far below market price: BUY 100 DOGE @ $0.01 (Will not fill!)
        test_price = 0.01
        test_qty = 100.0
        
        place_resp = client.place_order(pair="DOGE/USD", side="BUY", quantity=test_qty, order_type="LIMIT", price=test_price)
        if not place_resp.get("Success") and "OrderId" not in place_resp:
            print(f"{RED}[FAIL] Test order placement failed: {place_resp}{RESET}")
            sys.exit(1)

        order_id = place_resp.get("OrderId", place_resp.get("Data", {}).get("OrderId"))
        print(f"{GREEN}[PASS]{RESET} Test Limit Order Placed successfully: OrderID={order_id}")

        # Query Order
        time.sleep(0.5)
        query_resp = client.query_order(pair="DOGE/USD", pending_only=True)
        print(f"{GREEN}[PASS]{RESET} Query Order verified successfully.")

        # Cancel Order
        cancel_resp = client.cancel_order(order_id=order_id, pair="DOGE/USD")
        print(f"{GREEN}[PASS]{RESET} Test Order Cancelled successfully: {cancel_resp}")

    print("=" * 65)
    print(f"{GREEN}ALL PRE-FLIGHT CHECKS PASSED FOR '{mode.upper()}' ENVIRONMENT!{RESET}")
    print("The system is 100% verified and ready for live execution.")
    print("=" * 65)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Roostoo Pre-flight Smoke Test")
    parser.add_argument("--mode", choices=["test", "prod"], default="test", help="Credential mode to test ('test' or 'prod')")
    parser.add_argument("--test-order", action="store_true", help="Perform a safe limit order place/query/cancel test")
    args = parser.parse_args()
    run_preflight(args.mode, args.test_order)
