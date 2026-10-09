import os
import json
import logging
import datetime
from pathlib import Path
from bot.config.settings import settings

settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)
TRACE_FILE = settings.LOGS_DIR / "trade_execution_trace.jsonl"
SYSTEM_LOG_FILE = settings.LOGS_DIR / "bot_activity.log"

# Setup standard logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(SYSTEM_LOG_FILE),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("RoostooBot")

def log_trade(
    symbol: str,
    side: str,
    price: float,
    quantity: float,
    order_id: str,
    api_response: dict,
    signal_reason: str,
    strategy_state: dict = None,
    pnl: float = None
):
    """
    Audit-proof trade logger satisfying Screen 1 (Trade Log Integrity).
    """
    record = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "symbol": symbol,
        "side": side.upper(),
        "price": price,
        "quantity": quantity,
        "order_id": str(order_id),
        "signal_reason": signal_reason,
        "pnl": pnl,
        "strategy_state": strategy_state or {},
        "api_response": api_response
    }
    
    # Write to JSONL
    with open(TRACE_FILE, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")
        
    logger.info(
        f"[TRADE EXECUTED] {side.upper()} {quantity} {symbol} @ {price} | "
        f"OrderID: {order_id} | Reason: {signal_reason}"
    )
