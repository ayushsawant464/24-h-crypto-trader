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

        # --- Determine Market Regime & Core-Satellite Allocation ---
        # Look at BTC 12h return from snapshot if available
        btc_metric = snapshot.assets.get("BTCUSDT")
        btc_12h = btc_metric.return_12h_pct if btc_metric else 0.0

        if btc_12h > 1.0 and snapshot.btc_taker_buy_pct >= 48.0:
            regime = "BULL_EXPANSION"
            anchor_budget = settings.BULL_ANCHOR_WEIGHT        # 20%
            satellite_budget = settings.BULL_SATELLITE_WEIGHT  # 60%
        elif abs(btc_12h) <= 1.0:
            regime = "SIDEWAYS_STABILITY"
            anchor_budget = settings.SIDEWAYS_ANCHOR_WEIGHT    # 60%
            satellite_budget = settings.SIDEWAYS_SATELLITE_WEIGHT  # 20%
        else:
            regime = "BASELINE_BALANCED"
            anchor_budget = settings.BASELINE_ANCHOR_WEIGHT    # 40%
            satellite_budget = settings.BASELINE_SATELLITE_WEIGHT  # 40%

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
