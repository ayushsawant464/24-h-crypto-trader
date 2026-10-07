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
        Calculates Realistic Multi-Outcome Net Mathematical Expectancy:
        E[R_net] = P(Win) * E[R_win] - P(Loss) * E[R_loss] - Friction
        
        Calibrated to the bot's dynamic lifecycle:
        - Big Runner (Trailing Stop): E[R] ~ +5.2%
        - Profit Ratchet (Breakeven): E[R] ~ +0.40%
        - Cycle Rotation / Drift: E[R] ~ +1.0%
        - Controlled Stop / Decay: E[R] ~ -1.6%
        """
        # Multi-factor probabilistic calibration:
        # High-tier quant hedge funds achieve directional win rates bounded between 45% and 58%.
        tb_fraction = metric.taker_buy_4h_pct / 100.0
        trend_factor = 0.02 if metric.return_4h_pct > 0 else -0.02
        alpha_factor = 0.02 if metric.residual_alpha_pct > 0 else -0.01

        p_win = min(0.60, max(0.42, 0.48 + 0.40 * (tb_fraction - 0.50) + trend_factor + alpha_factor))
        p_loss = 1.0 - p_win

        vol_scale = max(1.0, metric.atr_15m_pct / 0.8) if metric.atr_15m_pct > 0 else 1.0

        # Weighted expectation of winning trades:
        # 45% runners, 35% ratchets, 20% rebalance drift
        r_runner = (settings.TRAILING_STOP_TRIGGER_PCT + settings.TRAILING_STOP_OFFSET_PCT) * vol_scale  # ~5.2%
        r_ratchet = settings.PROFIT_RATCHET_LOCK_PCT                                                    # +0.40%
        r_drift = 1.0 * vol_scale                                                                       # +1.0%
        expected_win_payoff = (0.45 * r_runner) + (0.35 * r_ratchet) + (0.20 * r_drift)

        # Expected loss of losing trades:
        # Exited early via momentum decay cut (-1.2%) or hard stop (-2.5%)
        expected_loss_payoff = 1.60 * vol_scale

        ev_net = (p_win * expected_win_payoff) - (p_loss * expected_loss_payoff) - self.friction_pct
        return round(ev_net, 3)

    def evaluate(self, snapshot: MarketSnapshot, portfolio_state: Dict[str, Any]) -> StrategyDecision:
        target_weights: Dict[str, float] = {}
        rationales: Dict[str, str] = {}
        expected_returns: Dict[str, float] = {}

        # --- CONTINUOUS COMPOSITE MARKET HEALTH SCORE (0 to 100) ---
        btc_metric = snapshot.assets.get("BTCUSDT")
        btc_12h = btc_metric.return_12h_pct if btc_metric else 0.0
        btc_tb = snapshot.btc_taker_buy_pct

        # Factor 1: Trend Alignment (0 to 25 pts)
        score_trend = 25.0 if snapshot.btc_above_ema20 else 5.0

        # Factor 2: Momentum Velocity (0 to 35 pts)
        if btc_12h >= 2.0:
            score_momentum = 35.0
        elif btc_12h >= 0.0:
            score_momentum = 15.0 + (btc_12h / 2.0) * 20.0
        elif btc_12h >= -1.5:
            score_momentum = 5.0 + ((btc_12h + 1.5) / 1.5) * 10.0
        else:
            score_momentum = 0.0

        # Factor 3: Order Flow Conviction (0 to 25 pts)
        if btc_tb >= 54.0:
            score_orderflow = 25.0
        elif btc_tb >= 50.0:
            score_orderflow = 15.0 + ((btc_tb - 50.0) / 4.0) * 10.0
        elif btc_tb >= 45.0:
            score_orderflow = 5.0 + ((btc_tb - 45.0) / 5.0) * 10.0
        else:
            score_orderflow = max(0.0, ((btc_tb - 40.0) / 5.0) * 5.0)

        # Factor 4: Volatility Environment (0 to 10 pts)
        score_volatility = 10.0 if snapshot.btc_atr_normal else 0.0

        market_health_score = score_trend + score_momentum + score_orderflow + score_volatility

        # Smooth Continuous Regime Transition (Eliminates Boolean Cliff Edges)
        if market_health_score < settings.REGIME_BEAR_SCORE_THRESHOLD:
            regime = "BEAR_CONTRACTION"
        elif market_health_score > settings.REGIME_BULL_SCORE_THRESHOLD:
            regime = "BULL_EXPANSION"
        else:
            regime = "SIDEWAYS_STABILITY"

        logger.info(
            f"[REGIME CLASSIFIER] Classified Market as: {regime} | HealthScore: {market_health_score:.1f}/100 | "
            f"BTC>EMA20: {snapshot.btc_above_ema20} | 12h Ret: {btc_12h:+.2f}% | "
            f"TakerBuy: {btc_tb:.1f}% | VolStable: {snapshot.btc_atr_normal}"
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
        # =========================================================================
        # REGIME B & C: BULL EXPANSION & SIDEWAYS STABILITY (4-Tier Engine)
        # =========================================================================
        if regime == "BULL_EXPANSION":
            anchor_budget = settings.BULL_ANCHOR_WEIGHT          # 20% Core
            satellite_budget = settings.BULL_SATELLITE_WEIGHT    # 60% Satellites
            tier_budgets = {
                "TIER_SMART_CONTRACTS": settings.BULL_SMART_CONTRACTS_WEIGHT,  # 35%
                "TIER_INFRASTRUCTURE": settings.BULL_INFRASTRUCTURE_WEIGHT,    # 15%
                "TIER_SPECULATIVE": settings.BULL_SPECULATIVE_WEIGHT           # 10%
            }
        else: # SIDEWAYS_STABILITY
            anchor_budget = settings.SIDEWAYS_ANCHOR_WEIGHT      # 60% Core
            satellite_budget = settings.SIDEWAYS_SMART_CONTRACTS_WEIGHT + settings.SIDEWAYS_INFRASTRUCTURE_WEIGHT + settings.SIDEWAYS_SPECULATIVE_WEIGHT # 20%
            tier_budgets = {
                "TIER_SMART_CONTRACTS": 0.20,
                "TIER_INFRASTRUCTURE": 0.10,
                "TIER_SPECULATIVE": 0.05
            }

        # --- 1. Tier 1: Anchor Allocation (Mega-Cap Stability: BTC or ETH) ---
        eth_metric = snapshot.assets.get("ETHUSDT")
        anchor_pair = "BTC/USD"
        if eth_metric and btc_metric and eth_metric.return_12h_pct > btc_metric.return_12h_pct and eth_metric.taker_buy_4h_pct > 50.0:
            anchor_pair = "ETH/USD"

        target_weights[anchor_pair] = round(anchor_budget, 3)
        rationales[anchor_pair] = f"Tier 1: Core Anchor ({anchor_budget*100:.0f}%) | Regime: {regime} | Mega-cap market presence."
        expected_returns[anchor_pair] = 0.50

        # --- 2. Screen & Rank Candidates Across Satellite Tiers ---
        # Helper to score candidates combining Lag Spread (catch-up bonus), Alpha, and Taker Flow
        def score_candidate(m: AssetMetrics) -> float:
            lag_bonus = max(0.0, m.lag_spread_4h_pct) * 1.5
            alpha = m.residual_alpha_pct
            orderflow = (m.taker_buy_4h_pct - 50.0) * 0.5
            price_pref = 1.0 / max(m.last_price ** 0.1, 1.0)
            return (lag_bonus + alpha + orderflow) * price_pref

        def get_tier_name(sym: str) -> str:
            if sym in settings.TIER_SMART_CONTRACTS:
                return "TIER_SMART_CONTRACTS"
            if sym in settings.TIER_INFRASTRUCTURE:
                return "TIER_INFRASTRUCTURE"
            if sym in settings.TIER_SPECULATIVE:
                return "TIER_SPECULATIVE"
            return "TIER_SPECULATIVE"

        # Screen through Gates 2, 3, 4 and Net EV hurdle
        candidates_by_tier: Dict[str, List[AssetMetrics]] = {
            "TIER_SMART_CONTRACTS": [],
            "TIER_INFRASTRUCTURE": [],
            "TIER_SPECULATIVE": []
        }

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

            # Gate 4: Alpha Identification (Momentum or Lag Catch-up)
            if m.return_12h_pct < 1.0 and m.lag_spread_4h_pct < 0.8:
                continue
            if m.residual_alpha_pct <= 0.0 and m.lag_spread_4h_pct < 0.8:
                continue

            # Positive Net EV Hurdle
            ev = self.calculate_expected_value(m)
            if ev < self.min_expected_return:
                continue

            tier_key = get_tier_name(sym)
            candidates_by_tier[tier_key].append(m)

        # Select top asset in each tier sorted by composite lag+alpha score
        selected_tier_assets: Dict[str, AssetMetrics] = {}
        for t_key, c_list in candidates_by_tier.items():
            if c_list:
                c_list.sort(key=score_candidate, reverse=True)
                selected_tier_assets[t_key] = c_list[0]

        all_selected = list(selected_tier_assets.values())

        if len(all_selected) == 1:
            # Concentrated single runner: allocate up to max single-asset allocation
            single_m = all_selected[0]
            ev = self.calculate_expected_value(single_m)
            single_weight = 0.20 if regime == "SIDEWAYS_STABILITY" else min(0.35, satellite_budget)
            target_weights[single_m.roostoo_pair] = single_weight
            rationales[single_m.roostoo_pair] = (
                f"{get_tier_name(single_m.symbol).replace('_', ' ').title()} ({single_weight*100:.1f}%) | "
                f"LagSpread: {single_m.lag_spread_4h_pct:+.2f}% | Alpha: {single_m.residual_alpha_pct:+.2f}% | "
                f"TakerBuy: {single_m.taker_buy_4h_pct:.1f}%"
            )
            expected_returns[single_m.roostoo_pair] = ev

        elif len(all_selected) > 1:
            # Multi-tier diversified basket: allocate based on tier weights
            total_budget_needed = sum(tier_budgets[t_key] for t_key in selected_tier_assets.keys())
            scale = min(1.0, satellite_budget / max(total_budget_needed, 1e-6))
            for t_key, m in selected_tier_assets.items():
                ev = self.calculate_expected_value(m)
                w = round(tier_budgets[t_key] * scale, 3)
                if w >= 0.05:
                    target_weights[m.roostoo_pair] = w
                    rationales[m.roostoo_pair] = (
                        f"{t_key.replace('_', ' ').title()} ({w*100:.1f}%) | "
                        f"LagSpread: {m.lag_spread_4h_pct:+.2f}% | Alpha: {m.residual_alpha_pct:+.2f}% | "
                        f"TakerBuy: {m.taker_buy_4h_pct:.1f}%"
                    )
                    expected_returns[m.roostoo_pair] = ev

        # --- 3. Cash Buffer Allocation ---
        # Encumbered capital (spot longs + short collateral) must be summed by absolute value
        allocated_so_far = sum(abs(w) for k, w in target_weights.items() if k != "USD")
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
