#!/usr/bin/env python3
"""
Roostoo Autonomous Quantitative Trading Bot
Entry point orchestrating the 8-Hour Rebalancing Engine and 1-Minute Risk Guard Daemon.
"""

import sys
import time
import signal
from typing import List
from bot.config.settings import settings
from bot.logs.logger import logger
from bot.data.roostoo_client import RoostooClient
from bot.data.market_feed import MarketFeed
from bot.strategy.decision_engine import DecisionEngine
from bot.execution.rebalancer import PortfolioRebalancer
from bot.execution.risk_guard import RiskGuard

# Core universe of liquid crypto assets on Roostoo
TRACKED_UNIVERSE = [
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ADAUSDT',
    'AVAXUSDT', 'NEARUSDT', 'DOGEUSDT', 'SUIUSDT', 'XRPUSDT',
    'LINKUSDT', 'TAOUSDT'
]

running = True

def handle_signal(sig, frame):
    global running
    logger.info(f"Received signal {sig}. Initiating graceful bot shutdown...")
    running = False

def run_bot(mode: str = None):
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    if mode:
        settings.switch_mode(mode)

    logger.info("==================================================")
    logger.info(f"Starting Roostoo Autonomous Quantitative Trading Bot (Mode: {settings.BOT_MODE.upper()})")
    logger.info(f"Target Base URL: {settings.ROOSTOO_BASE_URL}")
    logger.info(f"Rebalance Interval: {settings.REBALANCE_INTERVAL_HOURS} Hours")
    logger.info(f"Risk Audit Interval: {settings.RISK_CHECK_INTERVAL_SECONDS} Seconds")
    logger.info("==================================================")

    client = RoostooClient()
    
    # 1. Connectivity Check
    server_time_resp = client.get_server_time()
    if not server_time_resp.get("ServerTime"):
        logger.error(f"Failed to connect to Roostoo API: {server_time_resp}")
        sys.exit(1)
    logger.info(f"Connected to Roostoo Mock Exchange. ServerTime: {server_time_resp.get('ServerTime')}")

    # 2. Components Initialization
    feed = MarketFeed(client)
    strategy = DecisionEngine()
    rebalancer = PortfolioRebalancer(client)
    risk_guard = RiskGuard(client)

    ex_info = feed.get_exchange_info()
    logger.info(f"Loaded {len(ex_info)} trading pairs from Roostoo ExchangeInfo.")

    # 3. Initial Balance Check
    balance_resp = client.get_balance()
    wallet = balance_resp.get("Wallet", {})
    usd_val = float(wallet.get("USD", {}).get("Free", 0.0))
    logger.info(f"Initial Account Balance: Free USD = ${usd_val:,.2f}")

    # Timers
    rebalance_interval_sec = settings.REBALANCE_INTERVAL_HOURS * 3600
    last_rebalance_time = 0.0  # Force immediate first rebalance on startup

    logger.info("Entering autonomous dual-timeframe loop...")

    while running:
        try:
            now = time.time()

            # --- MICRO LOOP: Order Flow Snapshot & Risk Audit ---
            snapshot = feed.capture_snapshot(TRACKED_UNIVERSE)
            risk_status = risk_guard.audit_and_protect(snapshot)

            # --- MACRO LOOP: Portfolio Rebalance ---
            if (now - last_rebalance_time) >= rebalance_interval_sec:
                logger.info(f"[TRIGGER] Executing scheduled {settings.REBALANCE_INTERVAL_HOURS}-hour portfolio rebalance...")
                
                # Check current balance
                bal = client.get_balance()
                decision = strategy.evaluate(snapshot, bal)
                
                logger.info(f"Strategy Decision Regime: {decision.regime}")
                for pair, w in decision.target_weights.items():
                    logger.info(f"  Target Allocation: {pair:<10} -> {w*100:5.1f}% | {decision.rationales.get(pair, '')}")

                rebalancer.execute_rebalance(decision, snapshot.exchange_info)
                last_rebalance_time = now

            # Sleep for micro risk check interval
            time.sleep(settings.RISK_CHECK_INTERVAL_SECONDS)

        except Exception as e:
            logger.exception(f"Unexpected error in main loop: {e}")
            time.sleep(5)

    logger.info("Bot execution halted cleanly. Exiting.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Roostoo Quantitative Trading Bot")
    parser.add_argument("--mode", choices=["test", "prod"], default=None, help="Execution mode ('test' or 'prod')")
    args = parser.parse_args()
    run_bot(mode=args.mode)
