from typing import Dict, Any, List, Optional
from bot.config.settings import settings
from bot.data.market_feed import MarketSnapshot, AssetMetrics
from bot.strategy.base import BaseStrategy, StrategyDecision
from bot.logs.logger import logger

class DecisionEngine(BaseStrategy):
    """
    Quantitative Decision Engine implementing the 5 Calculating Gates
    and Positive Expected Value Hurdle from DECISION_CALCULUS.md.
    """
    def __init__(self, state_store: Optional[Any] = None):
        self.min_expected_return = settings.MIN_EXPECTED_NET_RETURN_PCT
        self.friction_pct = 0.22  # 2 * 0.1% fee + 0.02% typical spread
        if state_store is None:
            try:
                from bot.data.state_store import StateStore
                self.state_store = StateStore()
            except Exception:
                self.state_store = None
        else:
            self.state_store = state_store

    def calculate_expected_value(self, metric: AssetMetrics) -> float:
        """
        Calculates Realistic Multi-Outcome Net Mathematical Expectancy:
        E[R_net] = P(Win) * E[R_win] - P(Loss) * E[R_loss] - Friction
        
        Calibrated to the bot's dynamic lifecycle:
        - Big Runner (Trailing Stop): E[R] ~ (Trigger - Offset) * vol_scale (+2.8% to +3.5%)
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

        # Trailing stop base floor = Trigger (4.0%) - Offset (1.2%) = 2.8%
        # Empirical runner extension driven by alpha and cross-crypto lag catchup
        alpha_extension = max(0.0, metric.residual_alpha_pct, getattr(metric, 'lag_spread_4h_pct', 0.0))
        r_runner = (settings.TRAILING_STOP_TRIGGER_PCT - settings.TRAILING_STOP_OFFSET_PCT + alpha_extension) * 1.25 * vol_scale
        r_ratchet = max(settings.PROFIT_RATCHET_LOCK_PCT, 1.0 * vol_scale)
        r_drift = 1.0 * vol_scale
        expected_win_payoff = (0.45 * r_runner) + (0.35 * r_ratchet) + (0.20 * r_drift)

        # Expected loss of losing trades:
        # Anchored honestly to dynamic stop-loss: max(3.5%, 2.2 * ATR_15m) capped at 4.5%
        # Blended with early momentum decay exits (1.2% threshold)
        atr_val = metric.atr_15m_pct if metric.atr_15m_pct > 0 else 1.0
        dyn_stop = min(settings.MAX_ATR_STOP_LOSS_PCT, max(settings.HARD_STOP_LOSS_PCT, settings.DYNAMIC_ATR_MULTIPLIER * atr_val))
        expected_loss_payoff = (0.60 * settings.EARLY_MOMENTUM_DECAY_PCT + 0.40 * dyn_stop)

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

        def get_tier_name(sym: str) -> str:
            # Robust normalization: handles SOL/USD, SOLUSDT, and raw SOL
            clean_sym = sym.replace("/", "").replace("USD", "")
            if not clean_sym.endswith("USDT"):
                clean_sym = f"{clean_sym}USDT"

            if clean_sym in settings.TIER_SMART_CONTRACTS or sym in settings.TIER_SMART_CONTRACTS:
                return "TIER_SMART_CONTRACTS"
            if clean_sym in settings.TIER_INFRASTRUCTURE or sym in settings.TIER_INFRASTRUCTURE:
                return "TIER_INFRASTRUCTURE"
            if clean_sym in settings.TIER_SPECULATIVE or sym in settings.TIER_SPECULATIVE:
                return "TIER_SPECULATIVE"
            return "TIER_SPECULATIVE"

        # =========================================================================
        # REGIME A: BEAR CONTRACTION (Hedged Cross-Sectional Alpha & Partition Rebalancing)
        # =========================================================================
        if regime == "BEAR_CONTRACTION":
            # Screen for resilient partition candidates displaying positive residual alpha
            # and institutional taker accumulation despite overall market downturn.
            bear_candidates_by_tier: Dict[str, List[AssetMetrics]] = {
                "TIER_SMART_CONTRACTS": [],
                "TIER_INFRASTRUCTURE": [],
                "TIER_SPECULATIVE": []
            }

            for sym, m in snapshot.assets.items():
                if sym in ("BTCUSDT",):
                    continue  # Macro hedge benchmark handled separately

                # Asset Quarantine Filter
                if self.state_store and self.state_store.is_quarantined(m.roostoo_pair):
                    continue

                # Gate 2: Liquidity & Spread
                if not m.is_liquid or m.spread_pct > settings.MAX_SPREAD_PCT:
                    continue

                # Gate 3: Order Flow Accumulation (Whale absorption against the dump)
                if m.taker_buy_4h_pct < settings.MIN_TAKER_BUY_PCT:
                    continue
                if m.taker_imbalance_15m < 0.0:
                    continue
                if m.vol_zscore_15m > 2.5 and m.return_15m_pct < 0.0:
                    continue

                # Gate 4: Decoupled Residual Alpha (must outperform predicted market beta drop)
                if m.residual_alpha_pct < settings.BEAR_MIN_ALPHA_PCT:
                    continue

                # Gate 5: Positive Net EV Hurdle
                ev = self.calculate_expected_value(m)
                if ev < self.min_expected_return:
                    continue

                tier_key = get_tier_name(sym)
                bear_candidates_by_tier[tier_key].append(m)

            # Score bear candidates: Reward residual alpha & taker buying, penalize high beta (> 1.0)
            def score_bear_candidate(m: AssetMetrics) -> float:
                alpha_score = m.residual_alpha_pct * 1.5
                orderflow_score = (m.taker_buy_4h_pct - 50.0) * 0.6
                beta_penalty = max(0.0, m.beta_to_btc - 1.0) * 0.5
                return alpha_score + orderflow_score - beta_penalty

            selected_bear_assets: Dict[str, AssetMetrics] = {}
            for t_key, c_list in bear_candidates_by_tier.items():
                if c_list:
                    c_list.sort(key=score_bear_candidate, reverse=True)
                    selected_bear_assets[t_key] = c_list[0]

            bear_tier_budgets = {
                "TIER_SMART_CONTRACTS": settings.BEAR_SMART_CONTRACTS_WEIGHT,  # 15%
                "TIER_INFRASTRUCTURE": settings.BEAR_INFRASTRUCTURE_WEIGHT,    # 10%
                "TIER_SPECULATIVE": settings.BEAR_SPECULATIVE_WEIGHT           # 5%
            }

            total_long_beta = 0.0
            total_long_allocated = 0.0

            if selected_bear_assets:
                # Scale tier budgets to fit BEAR_LONG_BUDGET (30%)
                total_budget_needed = sum(bear_tier_budgets[t_key] for t_key in selected_bear_assets.keys())
                scale = min(1.0, settings.BEAR_LONG_BUDGET / max(total_budget_needed, 1e-6))

                for t_key, m in selected_bear_assets.items():
                    w = round(bear_tier_budgets[t_key] * scale, 3)
                    if w >= 0.02:
                        ev = self.calculate_expected_value(m)
                        target_weights[m.roostoo_pair] = w
                        rationales[m.roostoo_pair] = (
                            f"Bear Hedged Alpha ({t_key.replace('_', ' ').title()} {w*100:.1f}%) | "
                            f"Alpha: {m.residual_alpha_pct:+.2f}% | Beta: {m.beta_to_btc:.2f} | "
                            f"TakerBuy: {m.taker_buy_4h_pct:.1f}%"
                        )
                        expected_returns[m.roostoo_pair] = ev
                        total_long_allocated += w
                        total_long_beta += (w * m.beta_to_btc)

            # Macro Beta Hedge: Sized to offset total long portfolio beta
            if settings.ENABLE_SHORTING:
                if total_long_allocated > 0:
                    # Dynamically size Short BTC hedge to neutralize long portfolio beta with a 1.10x hedge ratio
                    hedge_short_w = min(
                        settings.BEAR_MAX_SHORT_HEDGE_WEIGHT,
                        max(settings.BEAR_SHORT_HEDGE_WEIGHT, round(total_long_beta * settings.BEAR_HEDGE_RATIO, 3))
                    )
                    target_weights["BTC/USD"] = -hedge_short_w
                    rationales["BTC/USD"] = (
                        f"Bear Macro Beta Hedge: -{hedge_short_w*100:.1f}% 1x Short BTC neutralizing "
                        f"long beta ({total_long_beta:.2f} @ {settings.BEAR_HEDGE_RATIO:.2f}x hedge ratio)."
                    )
                    expected_returns["BTC/USD"] = 1.50
                else:
                    # No resilient longs qualified: default to baseline 10% short hedge
                    target_weights["BTC/USD"] = -settings.BEAR_SHORT_HEDGE_WEIGHT
                    rationales["BTC/USD"] = "Bear Market Baseline Hedge: 10% 1x Short BTC to capture market downtrend."
                    expected_returns["BTC/USD"] = 1.50

            # Defensive Cash Buffer: remainder goes into USD Cash Bunker (at least 35% - 50%)
            encumbered = sum(abs(w) for k, w in target_weights.items() if k != "USD")
            target_weights["USD"] = round(max(settings.BEAR_MIN_CASH_BUFFER, 1.0 - encumbered), 3)
            rationales["USD"] = f"Bear Market Defense: {target_weights['USD']*100:.1f}% capital protected in USD Cash Bunker."
            expected_returns["USD"] = 0.0

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
            satellite_budget = settings.SIDEWAYS_SATELLITE_WEIGHT # 20%
            tier_budgets = {
                "TIER_SMART_CONTRACTS": settings.SIDEWAYS_SMART_CONTRACTS_WEIGHT,  # 12%
                "TIER_INFRASTRUCTURE": settings.SIDEWAYS_INFRASTRUCTURE_WEIGHT,    # 5%
                "TIER_SPECULATIVE": settings.SIDEWAYS_SPECULATIVE_WEIGHT           # 3%
            }

        # Check Bull Conditional Tail-Hedge Triggers (distribution / volatility shock)
        bull_tail_hedge_active = False
        bull_hedge_reasons: List[str] = []

        if regime == "BULL_EXPANSION" and settings.ENABLE_BULL_CONDITIONAL_HEDGE:
            # 1. Whale Distribution Filter: BTC taker buying collapses below threshold despite bull regime
            if snapshot.btc_taker_buy_pct < settings.BULL_HEDGE_TRIGGER_TAKER_BUY:
                bull_tail_hedge_active = True
                bull_hedge_reasons.append(f"BTC TakerBuy {snapshot.btc_taker_buy_pct:.1f}% < {settings.BULL_HEDGE_TRIGGER_TAKER_BUY}%")

            # 2. Volatility Turbulence / ATR Shock: Sudden volatility spike at resistance
            if settings.BULL_HEDGE_TRIGGER_ATR_EXPANSION and not snapshot.btc_atr_normal:
                bull_tail_hedge_active = True
                bull_hedge_reasons.append("BTC ATR Volatility Shock")

            # 3. Altcoin Dispersion Breakdown: Altcoins failing to follow BTC
            liquid_alt_alphas = [
                m.residual_alpha_pct for sym, m in snapshot.assets.items()
                if sym != "BTCUSDT" and m.is_liquid
            ]
            if liquid_alt_alphas:
                avg_alt_alpha = sum(liquid_alt_alphas) / len(liquid_alt_alphas)
                if avg_alt_alpha < settings.BULL_HEDGE_TRIGGER_ALT_DIVERGENCE:
                    bull_tail_hedge_active = True
                    bull_hedge_reasons.append(f"Altcoin Divergence (Avg Alpha: {avg_alt_alpha:+.2f}%)")

        # --- 1. Tier 1: Anchor Allocation (Mega-Cap Stability: BTC or ETH) ---
        eth_metric = snapshot.assets.get("ETHUSDT")
        anchor_pair = "BTC/USD"
        # Require absolute positive return (> 0.0%) so we never anchor to a bleeding asset
        if eth_metric and btc_metric:
            if eth_metric.return_12h_pct > btc_metric.return_12h_pct and eth_metric.return_12h_pct > 0.0 and eth_metric.taker_buy_4h_pct > 50.0:
                anchor_pair = "ETH/USD"

        if bull_tail_hedge_active:
            # When Bull Tail-Hedge is active, avoid long BTC due to detected distribution.
            # If ETH is showing genuine positive momentum and accumulation, retain a de-risked 10% anchor.
            if eth_metric and eth_metric.return_12h_pct > 0.0 and eth_metric.taker_buy_4h_pct > 50.0 and not (self.state_store and self.state_store.is_quarantined("ETH/USD")):
                target_weights["ETH/USD"] = 0.10
                rationales["ETH/USD"] = "Tier 1: Core Anchor (10.0%) | Bull Tail-Hedge Active: De-risked ETH anchor."
                expected_returns["ETH/USD"] = 0.50
            else:
                logger.info(f"[DECISION ENGINE] Bull Tail-Hedge active ({', '.join(bull_hedge_reasons)}). Anchor budget parked in USD Cash.")
                target_weights["USD"] = target_weights.get("USD", 0.0) + anchor_budget
                rationales["USD"] = f"Bull Tail-Hedge Defense: Anchor budget parked in Cash due to {', '.join(bull_hedge_reasons)}."
                expected_returns["USD"] = 0.0

            # Macro Short BTC Tail-Hedge:
            if settings.ENABLE_SHORTING:
                hedge_w = settings.BULL_CONDITIONAL_SHORT_HEDGE_WEIGHT
                target_weights["BTC/USD"] = -hedge_w
                rationales["BTC/USD"] = (
                    f"Bull Conditional Tail-Hedge: -{hedge_w*100:.1f}% 1x Short BTC protecting portfolio "
                    f"against {', '.join(bull_hedge_reasons)}."
                )
                expected_returns["BTC/USD"] = 1.20
        else:
            # Anchor Quarantine Check: Fallback to alternate mega-cap or USD cash if quarantined
            if self.state_store and self.state_store.is_quarantined(anchor_pair):
                alt_pair = "ETH/USD" if anchor_pair == "BTC/USD" else "BTC/USD"
                if not self.state_store.is_quarantined(alt_pair):
                    logger.info(f"[DECISION ENGINE] Anchor {anchor_pair} quarantined. Switching anchor to {alt_pair}.")
                    anchor_pair = alt_pair
                    target_weights[anchor_pair] = round(anchor_budget, 3)
                    rationales[anchor_pair] = f"Tier 1: Core Anchor ({anchor_budget*100:.0f}%) | Alternate anchor ({anchor_pair}) selected due to quarantine."
                    expected_returns[anchor_pair] = 0.50
                else:
                    logger.info(f"[DECISION ENGINE] Both BTC and ETH quarantined. Allocating anchor budget {anchor_budget*100:.0f}% to USD Cash.")
                    target_weights["USD"] = target_weights.get("USD", 0.0) + anchor_budget
                    rationales["USD"] = f"Anchor Quarantine Defense: Both BTC and ETH quarantined. Parking {anchor_budget*100:.0f}% in USD Cash."
                    expected_returns["USD"] = 0.0
            else:
                target_weights[anchor_pair] = round(anchor_budget, 3)
                rationales[anchor_pair] = f"Tier 1: Core Anchor ({anchor_budget*100:.0f}%) | Regime: {regime} | Mega-cap market presence."
                expected_returns[anchor_pair] = 0.50

        # --- 2. Screen & Rank Candidates Across Satellite Tiers ---
        # Helper to score candidates combining Lag Spread (catch-up bonus), Alpha, and Taker Flow
        # Free from nominal unit-price bias: Evaluates purely on momentum, orderflow, and statistical edge
        def score_candidate(m: AssetMetrics) -> float:
            lag_bonus = max(0.0, m.lag_spread_4h_pct) * 1.5
            alpha = m.residual_alpha_pct
            orderflow = (m.taker_buy_4h_pct - 50.0) * 0.5
            return lag_bonus + alpha + orderflow

        # Screen through Gates 2, 3, 4 and Net EV hurdle
        candidates_by_tier: Dict[str, List[AssetMetrics]] = {
            "TIER_SMART_CONTRACTS": [],
            "TIER_INFRASTRUCTURE": [],
            "TIER_SPECULATIVE": []
        }

        for sym, m in snapshot.assets.items():
            if sym in ("BTCUSDT",):
                continue  # Benchmark handled separately

            # Asset Quarantine Filter: do not repurchase stopped-out assets during cooloff
            if self.state_store and self.state_store.is_quarantined(m.roostoo_pair):
                logger.info(f"[DECISION ENGINE] Skipping {m.roostoo_pair} - under 12h stop-loss quarantine.")
                continue

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
            # Concentrated single runner: cap at MAX_ALTCOIN_ALLOCATION (20%) to avoid fat-tail risk
            single_m = all_selected[0]
            ev = self.calculate_expected_value(single_m)
            single_weight = min(settings.MAX_ALTCOIN_ALLOCATION, satellite_budget)
            target_weights[single_m.roostoo_pair] = single_weight
            rationales[single_m.roostoo_pair] = (
                f"{get_tier_name(single_m.symbol).replace('_', ' ').title()} ({single_weight*100:.1f}%) | "
                f"LagSpread: {single_m.lag_spread_4h_pct:+.2f}% | Alpha: {single_m.residual_alpha_pct:+.2f}% | "
                f"TakerBuy: {single_m.taker_buy_4h_pct:.1f}%"
            )
            expected_returns[single_m.roostoo_pair] = ev

        elif len(all_selected) > 1:
            # Multi-tier diversified basket: allocate based on tier weights, capped at MAX_ALTCOIN_ALLOCATION
            total_budget_needed = sum(tier_budgets[t_key] for t_key in selected_tier_assets.keys())
            scale = min(1.0, satellite_budget / max(total_budget_needed, 1e-6))
            for t_key, m in selected_tier_assets.items():
                ev = self.calculate_expected_value(m)
                w = min(settings.MAX_ALTCOIN_ALLOCATION, round(tier_budgets[t_key] * scale, 3))
                if w >= 0.02:
                    target_weights[m.roostoo_pair] = w
                    rationales[m.roostoo_pair] = (
                        f"{t_key.replace('_', ' ').title()} ({w*100:.1f}%) | "
                        f"LagSpread: {m.lag_spread_4h_pct:+.2f}% | Alpha: {m.residual_alpha_pct:+.2f}% | "
                        f"TakerBuy: {m.taker_buy_4h_pct:.1f}%"
                    )
                    expected_returns[m.roostoo_pair] = ev

        # --- 3. Target Weight Normalization & Cash Buffer Allocation ---
        # Encumbered capital (spot longs + short collateral) must be summed by absolute value
        allocated_so_far = sum(abs(w) for k, w in target_weights.items() if k != "USD")
        effective_min_cash = (
            settings.BULL_CONDITIONAL_CASH_BUFFER 
            if (regime == "BULL_EXPANSION" and bull_tail_hedge_active) 
            else settings.MIN_CASH_BUFFER
        )
        max_non_cash = round(1.0 - effective_min_cash, 4)
        
        # Guard against portfolio budget overrun (> max_non_cash):
        if allocated_so_far > max_non_cash and allocated_so_far > 0:
            scale = max_non_cash / allocated_so_far
            for k in list(target_weights.keys()):
                if k != "USD":
                    target_weights[k] = round(target_weights[k] * scale, 3)
            allocated_so_far = sum(abs(w) for k, w in target_weights.items() if k != "USD")

        cash_weight = max(effective_min_cash, round(1.0 - allocated_so_far, 3))
        target_weights["USD"] = cash_weight
        if bull_tail_hedge_active:
            rationales["USD"] = f"Elevated Cash Buffer ({cash_weight*100:.1f}%) protecting capital during Bull Tail-Hedge ({', '.join(bull_hedge_reasons)})."
        else:
            rationales["USD"] = f"Liquid Cash Buffer ({cash_weight*100:.1f}%) preserving capital and absorbing trading fees."
        expected_returns["USD"] = 0.0

        return StrategyDecision(
            target_weights=target_weights,
            regime=regime,
            rationales=rationales,
            expected_returns=expected_returns
        )
