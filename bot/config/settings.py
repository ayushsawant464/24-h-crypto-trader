import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root if present
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")

class Settings:
    # Environment Mode ('test' or 'prod')
    BOT_MODE: str = os.getenv("BOT_MODE", "test").lower()

    ROOSTOO_BASE_URL: str = os.getenv("ROOSTOO_BASE_URL", "https://mock-api.roostoo.com")

    # Dual API Credentials
    ROOSTOO_TEST_API_KEY: str = os.getenv("ROOSTOO_TEST_API_KEY", "")
    ROOSTOO_TEST_SECRET_KEY: str = os.getenv("ROOSTOO_TEST_SECRET_KEY", "")
    ROOSTOO_PROD_API_KEY: str = os.getenv("ROOSTOO_PROD_API_KEY", "")
    ROOSTOO_PROD_SECRET_KEY: str = os.getenv("ROOSTOO_PROD_SECRET_KEY", "")

    # Active Credentials (dynamically resolved)
    ROOSTOO_API_KEY: str = ""
    ROOSTOO_SECRET_KEY: str = ""

    def __init__(self):
        self._resolve_credentials()

    def _resolve_credentials(self):
        if self.BOT_MODE == "prod":
            self.ROOSTOO_API_KEY = self.ROOSTOO_PROD_API_KEY or os.getenv("ROOSTOO_API_KEY", "")
            self.ROOSTOO_SECRET_KEY = self.ROOSTOO_PROD_SECRET_KEY or os.getenv("ROOSTOO_SECRET_KEY", "")
        else:
            self.ROOSTOO_API_KEY = self.ROOSTOO_TEST_API_KEY or os.getenv("ROOSTOO_API_KEY", "")
            self.ROOSTOO_SECRET_KEY = self.ROOSTOO_TEST_SECRET_KEY or os.getenv("ROOSTOO_SECRET_KEY", "")

    def switch_mode(self, mode: str):
        """Switches active credentials between 'test' and 'prod'"""
        mode = mode.lower()
        if mode not in ("test", "prod"):
            raise ValueError(f"Invalid mode '{mode}'. Must be 'test' or 'prod'.")
        self.BOT_MODE = mode
        self._resolve_credentials()

    # Binance Public API for Market/Order Flow Feed
    BINANCE_BASE_URL: str = "https://api.binance.com"

    # Execution Timing
    REBALANCE_INTERVAL_HOURS: int = int(os.getenv("REBALANCE_INTERVAL_HOURS", "4"))
    RISK_CHECK_INTERVAL_SECONDS: int = int(os.getenv("RISK_CHECK_INTERVAL_SECONDS", "60"))
    
    # Continuous Regime Classifier Thresholds (Market Health Score 0 to 100)
    REGIME_BULL_SCORE_THRESHOLD: float = 65.0   # Score > 65: Bull Expansion
    REGIME_BEAR_SCORE_THRESHOLD: float = 38.0   # Score < 38: Bear Contraction
    
    # Decision Gates Thresholds
    MIN_24H_VOL_USD: float = 5_000_000.0        # Gate 2: Liquidity filter
    MAX_SPREAD_PCT: float = 0.035               # Gate 2: Max 0.035% bid-ask spread
    MIN_TAKER_BUY_PCT: float = 51.0             # Gate 3: Minimum informed taker buy %
    MIN_EXPECTED_NET_RETURN_PCT: float = 0.20   # Core Hurdle: Realistic Multi-Outcome Net Expectancy (20 bps)
    
    # Toxicity Confirmation & Early Momentum Decay Thresholds
    TOXICITY_PRICE_DROP_CONFIRMATION_PCT: float = 0.50  # Must see active price drop to confirm dump
    EARLY_MOMENTUM_DECAY_PCT: float = 1.20              # Cut deteriorating positions before full 3.5% stop
    
    # Portfolio Allocation Constraints
    MAX_ALLOCATION_PER_ASSET: float = 0.40      # Max 40% for BTC Anchor Core
    MAX_ALTCOIN_ALLOCATION: float = 0.20        # Max 20% for any single altcoin (fat-tail defense)
    MAX_TOTAL_INVESTED: float = 0.80            # Max 80% total exposure
    MIN_CASH_BUFFER: float = 0.20               # Keep 20% in USD Cash Buffer
    
    # 4-Tier Categorical Portfolio Structure
    TIER_ANCHOR = ["BTCUSDT", "ETHUSDT"]
    TIER_SMART_CONTRACTS = ["ETHUSDT", "SOLUSDT", "SUIUSDT"]
    TIER_INFRASTRUCTURE = ["LINKUSDT", "TAOUSDT", "NEARUSDT"]
    TIER_SPECULATIVE = ["DOGEUSDT", "AVAXUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT"]

    # Baseline / Sideways Regime Weights (Anchor 60% : Satellite 20% : Cash 20%)
    SIDEWAYS_ANCHOR_WEIGHT: float = 0.60        # 60% Anchor Core (BTC)
    SIDEWAYS_SATELLITE_WEIGHT: float = 0.20     # 20% Total Satellite Budget
    SIDEWAYS_SMART_CONTRACTS_WEIGHT: float = 0.12 # Tier 2: Smart Contract Platforms
    SIDEWAYS_INFRASTRUCTURE_WEIGHT: float = 0.05  # Tier 3: Infrastructure & AI
    SIDEWAYS_SPECULATIVE_WEIGHT: float = 0.03    # Tier 4: Speculative minimized in sideways
    SIDEWAYS_CASH_WEIGHT: float = 0.20          # 20% Free USD Cash Buffer

    # Bull Expansion Regime Weights (Anchor 20% : Satellites 60% : Cash 20%)
    BULL_ANCHOR_WEIGHT: float = 0.20            # 20% Anchor Core (BTC)
    BULL_SATELLITE_WEIGHT: float = 0.60         # 60% Total Satellite Budget
    BULL_SMART_CONTRACTS_WEIGHT: float = 0.35   # Tier 2: Smart Contract Platforms
    BULL_INFRASTRUCTURE_WEIGHT: float = 0.15    # Tier 3: Infrastructure & AI
    BULL_SPECULATIVE_WEIGHT: float = 0.10       # Tier 4: High-Beta Retail Plays
    BULL_CASH_WEIGHT: float = 0.20              # 20% Free USD Cash Buffer

    # Bull Market Conditional Tail-Hedge Settings
    ENABLE_BULL_CONDITIONAL_HEDGE: bool = True
    BULL_HEDGE_TRIGGER_TAKER_BUY: float = 48.0       # Trigger: BTC taker buy < 48% signals whale distribution
    BULL_HEDGE_TRIGGER_ATR_EXPANSION: bool = True     # Trigger: BTC ATR surge/volatility shock
    BULL_HEDGE_TRIGGER_ALT_DIVERGENCE: float = -0.80 # Trigger: Average altcoin residual alpha < -0.80%
    BULL_CONDITIONAL_SHORT_HEDGE_WEIGHT: float = 0.10 # 10% Short BTC tail-hedge when warning lights fire
    BULL_CONDITIONAL_CASH_BUFFER: float = 0.30       # Elevate cash buffer to 30% during distribution warning

    # Bear Market Regime Weights (Hedged Cross-Sectional Alpha & Partition Rebalancing)
    BEAR_CASH_WEIGHT: float = 0.90              # Fallback 90% Cash Bunker when no partition alpha exists
    BEAR_GOLD_WEIGHT: float = 0.00              # 0.0% PAXG/USD (Eliminated: Roostoo mock exchange has no gold pairs)
    BEAR_LONG_BUDGET: float = 0.30              # Max 30% Long exposure across resilient partition candidates
    BEAR_SMART_CONTRACTS_WEIGHT: float = 0.15   # Tier 2: Smart Contract Platforms in Bear (up to 15%)
    BEAR_INFRASTRUCTURE_WEIGHT: float = 0.10    # Tier 3: Infrastructure & AI in Bear (up to 10%)
    BEAR_SPECULATIVE_WEIGHT: float = 0.05       # Tier 4: Speculative Beta (capped tightly at 5% in Bear)
    BEAR_SHORT_HEDGE_WEIGHT: float = 0.10       # Minimum 10% Short BTC Hedge (scales up to match portfolio beta)
    BEAR_MAX_SHORT_HEDGE_WEIGHT: float = 0.35   # Max 35% Short BTC Hedge
    BEAR_HEDGE_RATIO: float = 1.10              # 1.10x hedge ratio (slight net-short / beta-neutral bias in Bear)
    BEAR_MIN_ALPHA_PCT: float = 0.40            # Minimum residual alpha of +0.40% to qualify in bear regime
    BEAR_MIN_CASH_BUFFER: float = 0.35          # Strict 35% minimum cash buffer in Bear for drawdown defense
    ENABLE_SHORTING: bool = True
    MIN_BEAR_SHORT_DOWNSIDE_PCT: float = 1.5    # Minimum expected downside % to overcome fee friction
    SHORT_STOP_LOSS_PCT: float = 2.5            # Tighter stop-loss for short positions (2.5%)
    
    # Risk Management & Stop Limits
    HARD_STOP_LOSS_PCT: float = 3.5             # 3.5% baseline spot stop-loss
    DYNAMIC_ATR_MULTIPLIER: float = 2.2         # 2.2x 15m ATR for adaptive volatility stop
    MAX_ATR_STOP_LOSS_PCT: float = 4.5          # 4.5% hard cap on dynamic stop-loss
    PROFIT_RATCHET_TRIGGER_PCT: float = 2.0     # When profit >= +2.0%
    PROFIT_RATCHET_LOCK_PCT: float = 0.40       # Lock stop to +0.40% (covers 0.2% fees)
    TRAILING_STOP_TRIGGER_PCT: float = 4.0      # When profit >= +4.0%
    TRAILING_STOP_OFFSET_PCT: float = 1.20      # Trail 1.2% below peak
    PORTFOLIO_CIRCUIT_BREAKER_PCT: float = 5.0  # Max 5% total drawdown -> 100% Cash (widened from 2% to absorb ordinary intraday noise)
    CIRCUIT_BREAKER_COOLDOWN_HOURS: int = 4
    
    # Rebalance Execution & Hysteresis Defense
    REBALANCE_DEADBAND_PCT: float = 1.5         # 1.5% max portfolio deadband to prevent churn/overtrading
    QUARANTINE_DURATION_HOURS: float = 12.0     # 12-hour cooloff after stop-loss execution
    MAX_CONCURRENT_LIQUIDATION_WORKERS: int = 2 # Worker throttle to prevent HTTP 429 rate limit bans

    # Logging
    LOGS_DIR: Path = PROJECT_ROOT / "logs"

settings = Settings()
