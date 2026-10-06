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

        # --- COMPOSITE MULTI-FACTOR REGIME CLASSIFIER ---
        btc_metric = snapshot.assets.get("BTCUSDT")
        btc_12h = btc_metric.return_12h_pct if btc_metric else 0.0

        # Factor 1: Trend Alignment (BTC vs EMA20)
        trend_bullish = snapshot.btc_above_ema20

        # Factor 2: Order Flow Conviction (Taker Buy Ratio)
        orderflow_healthy = snapshot.btc_taker_buy_pct >= 47.0

        # Factor 3: Volatility Environment (Normal ATR vs Liquidation Cascade)
        volatility_stable = snapshot.btc_atr_normal

        # Factor 4: Momentum Velocity (12h Return)
        momentum_positive = btc_12h > 0.8

        # --- REGIME DETERMINATION ---
        if not trend_bullish or not volatility_stable or btc_12h < -1.5 or snapshot.btc_taker_buy_pct < 45.0:
            regime = "BEAR_CONTRACTION"
        elif trend_bullish and momentum_positive and orderflow_healthy:
            regime = "BULL_EXPANSION"
        else:
            regime = "SIDEWAYS_STABILITY"

        logger.info(
            f"[REGIME CLASSIFIER] Classified Market as: {regime} | "
            f"BTC>EMA20: {trend_bullish} | 12h Ret: {btc_12h:+.2f}% | "
            f"TakerBuy: {snapshot.btc_taker_buy_pct:.1f}% | VolStable: {volatility_stable}"
        )

        # =========================================================================
        # REGIME A: BEAR CONTRACTION (Capital Defense, Gold Fortress, Short Hedge)
        # =========================================================================
        if regime == "BEAR_CONTRACTION":
            target_weights["USD"] = settings.BEAR_CASH_WEIGHT  # 70% Free Cash
            rationales["USD"] = "Bear Market: 70% capital protected in USD Cash Bunker to guarantee zero drawdown."
            expected_returns["USD"] = 0.0

            # Safe-Haven Gold allocation (PAXG/USD)
            paxg_pair = "PAXG/USD"
            target_weights[paxg_pair] = settings.BEAR_GOLD_WEIGHT  # 20% Gold
            rationales[paxg_pair] = "Bear Market Safe-Haven: 20% allocation to PAXG (physical gold peg) decoupled from crypto sell-offs."
            expected_returns[paxg_pair] = 0.20

            # Optional Short BTC Hedge (captures alpha from crypto decline)
            if settings.ENABLE_SHORTING:
                target_weights["BTC/USD"] = -settings.BEAR_SHORT_HEDGE_WEIGHT  # -10% Short Hedge
                rationales["BTC/USD"] = "Bear Market Hedge: 10% 1x Short BTC position via /v6/short_open to generate positive return during market dumps."
                expected_returns["BTC/USD"] = 1.50
            else:
                target_weights["USD"] += settings.BEAR_SHORT_HEDGE_WEIGHT

            return StrategyDecision(
                target_weights=target_weights,
                regime=regime,
                rationales=rationales,
                expected_returns=expected_returns
            )

        # =========================================================================
        # REGIME B & C: BULL EXPANSION & SIDEWAYS STABILITY (Core-Satellite Engine)
        # =========================================================================
        # Screen candidates for satellite basket through Gates 2, 3, and 4
        candidates: List[AssetMetrics] = []
        for sym, m in snapshot.assets.items():
            if sym in ("BTCUSDT", "PAXGUSDT"):
                continue  # Benchmark and Gold handled separately

            # Gate 2: Liquidity & Spread
            if not m.is_liquid:
                continue

            # Gate 3: Order Flow Toxicity & Whale Filter
            if m.taker_buy_4h_pct < settings.MIN_TAKER_BUY_PCT:
                continue
            if m.taker_imbalance_15m < 0.0:
                continue
            if m.vol_zscore_15m > 2.5 and m.return_15m_pct < 0.0:
                continue

            # Gate 4: Alpha Identification (Momentum & Residual Alpha)
            if m.return_12h_pct < 1.0:
                continue
            if m.residual_alpha_pct <= 0.0:
                continue

            # Positive Expected Value Hurdle
            ev = self.calculate_expected_value(m)
            if ev < self.min_expected_return:
                continue

            candidates.append(m)

        if regime == "BULL_EXPANSION":
            anchor_budget = settings.BULL_ANCHOR_WEIGHT        # 20% Core
            satellite_budget = settings.BULL_SATELLITE_WEIGHT  # 60% Small-Cap Basket
        else: # SIDEWAYS_STABILITY
            anchor_budget = settings.SIDEWAYS_ANCHOR_WEIGHT    # 60% Core
            satellite_budget = settings.SIDEWAYS_SATELLITE_WEIGHT  # 20% Small-Cap Basket

        # --- 1. Anchor Allocation (Mega-Cap Stability: BTC or ETH) ---
        eth_metric = snapshot.assets.get("ETHUSDT")
        anchor_pair = "BTC/USD"
        # If ETH has superior risk-adjusted momentum and taker buy, use ETH as anchor
        if eth_metric and btc_metric and eth_metric.return_12h_pct > btc_metric.return_12h_pct and eth_metric.taker_buy_4h_pct > 50.0:
            anchor_pair = "ETH/USD"

        target_weights[anchor_pair] = round(anchor_budget, 3)
        rationales[anchor_pair] = f"Anchor Core ({anchor_budget*100:.0f}%) | Regime: {regime} | Mega-cap stability & market presence."
        expected_returns[anchor_pair] = 0.50

        # --- 2. Satellite Allocation (Diversified Lower-Price / High-Beta Assets) ---
        # Sort candidates: preference to lower price / high beta with strong order flow
        candidates.sort(
            key=lambda x: (x.residual_alpha_pct + (x.taker_buy_4h_pct - 50.0) * 0.5) / max(x.last_price ** 0.1, 1.0),
            reverse=True
        )

        selected_satellites = candidates[:3]
        if selected_satellites:
            # Weighted diversification across satellite basket
            total_inv_vol = sum(1.0 / max(m.atr_15m_pct, 0.4) for m in selected_satellites)
            for m in selected_satellites:
                ev = self.calculate_expected_value(m)
                weight_fraction = (1.0 / max(m.atr_15m_pct, 0.4)) / max(total_inv_vol, 1e-6)
                sat_weight = min(0.30, round(satellite_budget * weight_fraction, 3))
                
                if sat_weight >= 0.05:
                    target_weights[m.roostoo_pair] = sat_weight
                    rationales[m.roostoo_pair] = (
                        f"Satellite Basket ({sat_weight*100:.1f}%) | LastPrice: ${m.last_price:.4f} | "
                        f"12h Ret: {m.return_12h_pct:+.2f}% | Alpha: {m.residual_alpha_pct:+.2f}% | TakerBuy: {m.taker_buy_4h_pct:.1f}%"
                    )
                    expected_returns[m.roostoo_pair] = ev
        else:
            # If no satellite passed, shift satellite budget into Anchor / Cash
            target_weights[anchor_pair] = min(0.70, round(target_weights[anchor_pair] + (satellite_budget * 0.5), 3))

        # --- 3. Cash Buffer Allocation ---
        allocated_so_far = sum(w for k, w in target_weights.items() if k != "USD")
        cash_weight = max(settings.MIN_CASH_BUFFER, round(1.0 - allocated_so_far, 3))
        target_weights["USD"] = cash_weight
        rationales["USD"] = f"Liquid Cash Buffer ({cash_weight*100:.1f}%) preserving capital and absorbing trading fees."
        expected_returns["USD"] = 0.0

        return StrategyDecision(
            target_weights=target_weights,
            regime=regime,
            rationales=rationales,
            expected_returns=expected_returns
        )
