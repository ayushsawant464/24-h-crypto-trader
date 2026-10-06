from typing import Dict, Any, List
from bot.config.settings import settings
from bot.data.market_feed import MarketSnapshot, AssetMetrics
from bot.strategy.base import BaseStrategy, StrategyDecision
from bot.logs.logger import logger

class DecisionEngine(BaseStrategy):
    """
    Quantitative Decision Engine implementing the 5 Calculating Gates
    and Positive Expected Value Hurdle from DECISION_CALCULUS.md.
    """
    def __init__(self):
        self.min_expected_return = settings.MIN_EXPECTED_NET_RETURN_PCT
        self.friction_pct = 0.22  # 2 * 0.1% fee + 0.02% typical spread

    def calculate_expected_value(self, metric: AssetMetrics) -> float:
        """
        Calculates Net Mathematical Expectancy:
        E[R_net] = P(Win) * R_win - P(Loss) * R_loss - Friction
        """
        # Base win probability calibrated by empirical order flow findings:
        # P(Win) increases linearly with informed taker buy volume
        tb_fraction = metric.taker_buy_4h_pct / 100.0
        p_win = min(0.75, max(0.40, 0.50 + 0.50 * (tb_fraction - 0.50)))
        p_loss = 1.0 - p_win

        target_win_pct = 4.0
        target_loss_pct = 2.0

        ev_net = (p_win * target_win_pct) - (p_loss * target_loss_pct) - self.friction_pct
        return round(ev_net, 3)

    def evaluate(self, snapshot: MarketSnapshot, portfolio_state: Dict[str, Any]) -> StrategyDecision:
        target_weights: Dict[str, float] = {}
        rationales: Dict[str, str] = {}
        expected_returns: Dict[str, float] = {}

        # --- GATE 1: Macro & Regime Permission ---
        macro_passed = (
            snapshot.btc_above_ema20 and
            snapshot.btc_taker_buy_pct >= 45.0 and
            snapshot.btc_atr_normal
        )

        if not macro_passed:
            logger.warning(
                f"[GATE 1 FAILED] BTC below EMA20 or high volatility cascade. "
                f"Activating Cash Bunker / Protective Hedge."
            )
            # Allocate 100% to USD Cash (or small BTC short hedge if allowed)
            return StrategyDecision(
                target_weights={"USD": 1.0},
                regime="CASH_BUNKER",
                rationales={"USD": "Macro regime failure: BTC below EMA20. Capital parked in Cash Bunker."},
                expected_returns={"USD": 0.0}
            )

        # --- Screen Assets through Gates 2, 3, and 4 ---
        candidates: List[AssetMetrics] = []
        for sym, m in snapshot.assets.items():
            if sym == "BTCUSDT":
                continue  # BTC is our macro benchmark, not a momentum alt

            # GATE 2: Liquidity & Spread
            if not m.is_liquid:
                continue

            # GATE 3: Order Flow Toxicity & Whale Filter
            if m.taker_buy_4h_pct < settings.MIN_TAKER_BUY_PCT:
                continue
            if m.taker_imbalance_15m < 0.0:
                continue
            if m.vol_zscore_15m > 2.5 and m.return_15m_pct < 0.0:
                # Toxic insider/whale selling spike detected
                continue

            # GATE 4: Alpha Identification (Momentum & Residual Alpha)
            if m.return_12h_pct < 1.5:
                continue
            if m.residual_alpha_pct <= 0.0:
                continue

            # Compute Expected Net Value
            ev = self.calculate_expected_value(m)
            if ev < self.min_expected_return:
                continue

            candidates.append(m)

        # Sort candidates by Residual Alpha and Momentum
        candidates.sort(
            key=lambda x: (x.residual_alpha_pct + (x.taker_buy_4h_pct - 50.0) * 0.5),
            reverse=True
        )

        # Select Top 2 to Top 3 Assets
        selected = candidates[:3]

        if not selected:
            logger.info("No candidate assets passed all 5 gates. Holding 100% USD Cash.")
            return StrategyDecision(
                target_weights={"USD": 1.0},
                regime="MARKET_NEUTRAL_CASH",
                rationales={"USD": "No asset passed the positive expectancy threshold. Holding 100% Cash."},
                expected_returns={"USD": 0.0}
            )

        # --- GATE 5: Volatility-Parity Position Sizing ---
        total_allocated = 0.0
        for m in selected:
            ev = self.calculate_expected_value(m)
            # Target 1.0% risk / 15m ATR
            raw_weight = 1.0 / max(m.atr_15m_pct, 0.5)
            # Cap at 30% per asset
            weight = min(settings.MAX_ALLOCATION_PER_ASSET, max(0.15, raw_weight * 0.20))
            
            # Ensure total doesn't exceed 80% (preserving 20% cash)
            if total_allocated + weight > settings.MAX_TOTAL_INVESTED:
                weight = max(0.0, settings.MAX_TOTAL_INVESTED - total_allocated)

            if weight >= 0.10:
                target_weights[m.roostoo_pair] = round(weight, 3)
                total_allocated += weight
                rationales[m.roostoo_pair] = (
                    f"Passed all 5 gates | 12h Ret: {m.return_12h_pct:.2f}% | "
                    f"Taker Buy: {m.taker_buy_4h_pct:.1f}% | Alpha: {m.residual_alpha_pct:.2f}% | E[R]: +{ev:.2f}%"
                )
                expected_returns[m.roostoo_pair] = ev

        # Remaining capital in USD Cash
        cash_weight = max(settings.MIN_CASH_BUFFER, 1.0 - total_allocated)
        target_weights["USD"] = round(cash_weight, 3)
        rationales["USD"] = f"Preserving {cash_weight*100:.1f}% cash buffer to guarantee zero drawdown on reserves."
        expected_returns["USD"] = 0.0

        return StrategyDecision(
            target_weights=target_weights,
            regime="BULL_MOMENTUM_ALPHA",
            rationales=rationales,
            expected_returns=expected_returns
        )
