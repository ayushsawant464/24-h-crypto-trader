import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root if present
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")

class Settings:
    # API Credentials
    ROOSTOO_BASE_URL: str = os.getenv("ROOSTOO_BASE_URL", "https://mock-api.roostoo.com")
    ROOSTOO_API_KEY: str = os.getenv("ROOSTOO_API_KEY", "")
    ROOSTOO_SECRET_KEY: str = os.getenv("ROOSTOO_SECRET_KEY", "")
    
    # Binance Public API for Market/Order Flow Feed
    BINANCE_BASE_URL: str = "https://api.binance.com"

    # Execution Timing
    REBALANCE_INTERVAL_HOURS: int = int(os.getenv("REBALANCE_INTERVAL_HOURS", "8"))
    RISK_CHECK_INTERVAL_SECONDS: int = int(os.getenv("RISK_CHECK_INTERVAL_SECONDS", "60"))
    
    # Decision Gates Thresholds
    MIN_24H_VOL_USD: float = 5_000_000.0        # Gate 2: Liquidity filter
    MAX_SPREAD_PCT: float = 0.035               # Gate 2: Max 0.035% bid-ask spread
    MIN_TAKER_BUY_PCT: float = 51.0             # Gate 3: Minimum informed taker buy %
    MIN_EXPECTED_NET_RETURN_PCT: float = 0.80   # Core Hurdle: Positive Net Expectancy
    
    # Portfolio Allocation Rules (Core-Satellite Architecture)
    MAX_ALLOCATION_PER_ASSET: float = 0.40      # Max 40% for anchor / 30% for satellite
    MAX_TOTAL_INVESTED: float = 0.80            # Max 80% total exposure
    MIN_CASH_BUFFER: float = 0.20               # Keep 20% in USD Cash
    
    # Regime Weights: Anchor (BTC/ETH) : Satellite (Small-Caps) : Cash
    BASELINE_ANCHOR_WEIGHT: float = 0.40        # 40% Core
    BASELINE_SATELLITE_WEIGHT: float = 0.40     # 40% Small-Cap Basket
    BULL_ANCHOR_WEIGHT: float = 0.20            # 20% Core in Bull
    BULL_SATELLITE_WEIGHT: float = 0.60         # 60% Small-Cap Basket in Bull
    SIDEWAYS_ANCHOR_WEIGHT: float = 0.60        # 60% Core in Sideways
    SIDEWAYS_SATELLITE_WEIGHT: float = 0.20     # 20% Small-Cap Basket in Sideways
    
    # Risk Management & Stop Limits
    HARD_STOP_LOSS_PCT: float = 3.5             # 3.5% initial stop-loss
    PROFIT_RATCHET_TRIGGER_PCT: float = 2.0     # When profit >= +2.0%
    PROFIT_RATCHET_LOCK_PCT: float = 0.40       # Lock stop to +0.40% (covers 0.2% fees)
    TRAILING_STOP_TRIGGER_PCT: float = 4.0      # When profit >= +4.0%
    TRAILING_STOP_OFFSET_PCT: float = 1.20      # Trail 1.2% below peak
    PORTFOLIO_CIRCUIT_BREAKER_PCT: float = 2.0  # Max 2% total drawdown -> 100% Cash
    CIRCUIT_BREAKER_COOLDOWN_HOURS: int = 4
    
    # Logging
    LOGS_DIR: Path = PROJECT_ROOT / "logs"

settings = Settings()
