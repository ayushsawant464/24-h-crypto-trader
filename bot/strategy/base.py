from abc import ABC, abstractmethod
from typing import Dict, Any
from dataclasses import dataclass
from bot.data.market_feed import MarketSnapshot

@dataclass
class StrategyDecision:
    target_weights: Dict[str, float]  # pair -> target portfolio fraction (e.g. 'SOL/USD': 0.25)
    regime: str                       # 'BULL_MOMENTUM', 'MARKET_NEUTRAL_HEDGE', 'CASH_BUNKER'
    rationales: Dict[str, str]        # pair -> explanation
    expected_returns: Dict[str, float]# pair -> net expected value

class BaseStrategy(ABC):
    @abstractmethod
    def evaluate(self, snapshot: MarketSnapshot, portfolio_state: Dict[str, Any]) -> StrategyDecision:
        pass
