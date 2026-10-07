import unittest
from bot.strategy.decision_engine import DecisionEngine
from bot.data.market_feed import MarketSnapshot, AssetMetrics

class TestDecisionEngine(unittest.TestCase):
    def test_decision_engine_bear_contraction(self):
        """
        Validates that when BTC drops below EMA20 or experiences heavy selling,
        the Bear Contraction regime triggers: 70% USD Cash, 20% PAXG Gold, and -10% Short BTC Hedge.
        """
        engine = DecisionEngine()
        
        # Snapshot where BTC is below EMA20 and has weak taker buy
        snapshot = MarketSnapshot(
            timestamp=1000000.0,
            btc_above_ema20=False,
            btc_taker_buy_pct=42.0,
            btc_atr_normal=True,
            assets={},
            exchange_info={}
        )

        decision = engine.evaluate(snapshot, portfolio_state={})
        self.assertEqual(decision.regime, "BEAR_CONTRACTION")
        self.assertEqual(decision.target_weights["USD"], 0.70)
        self.assertEqual(decision.target_weights["PAXG/USD"], 0.20)
        self.assertEqual(decision.target_weights["BTC/USD"], -0.10)

    def test_decision_engine_core_satellite_sideways(self):
        """
        Validates that in sideways markets (BTC flat), allocation shifts to:
        60% Anchor Core, 20% Satellite Basket, 20% Cash.
        """
        engine = DecisionEngine()

        sol_metric = AssetMetrics(
            symbol="SOLUSDT",
            roostoo_pair="SOL/USD",
            last_price=120.0,
            spread_pct=0.010,
            volume_24h_usd=50_000_000.0,
            return_12h_pct=4.5,
            return_4h_pct=2.0,
            return_15m_pct=0.5,
            taker_buy_4h_pct=53.5,
            taker_imbalance_15m=0.15,
            vol_zscore_15m=1.0,
            atr_15m_pct=0.8,
            beta_to_btc=1.1,
            residual_alpha_pct=3.2,
            is_liquid=True
        )

        snapshot = MarketSnapshot(
            timestamp=1000000.0,
            btc_above_ema20=True,
            btc_taker_buy_pct=48.0,
            btc_atr_normal=True,
            assets={"SOLUSDT": sol_metric},
            exchange_info={}
        )

        decision = engine.evaluate(snapshot, portfolio_state={})
        self.assertEqual(decision.regime, "SIDEWAYS_STABILITY")
        self.assertIn("BTC/USD", decision.target_weights)
        self.assertEqual(decision.target_weights["BTC/USD"], 0.60)  # 60% Anchor Core
        self.assertIn("SOL/USD", decision.target_weights)
        self.assertEqual(decision.target_weights["SOL/USD"], 0.20)  # 20% Satellite Basket
        self.assertEqual(decision.target_weights["USD"], 0.20)      # 20% Cash Buffer

    def test_decision_engine_core_satellite_bull(self):
        """
        Validates that in bull markets (BTC > 1.0% with strong taker buy),
        allocation shifts to 20% Anchor, 60% Satellite (Small-cap runners), 20% Cash.
        """
        engine = DecisionEngine()

        btc_metric = AssetMetrics(
            symbol="BTCUSDT",
            roostoo_pair="BTC/USD",
            last_price=86000.0,
            spread_pct=0.001,
            volume_24h_usd=1_000_000_000.0,
            return_12h_pct=2.5,
            return_4h_pct=1.5,
            return_15m_pct=0.2,
            taker_buy_4h_pct=52.0,
            taker_imbalance_15m=0.10,
            vol_zscore_15m=0.5,
            atr_15m_pct=0.4,
            beta_to_btc=1.0,
            residual_alpha_pct=0.0,
            is_liquid=True
        )

        ada_metric = AssetMetrics(
            symbol="ADAUSDT",
            roostoo_pair="ADA/USD",
            last_price=0.28,
            spread_pct=0.015,
            volume_24h_usd=30_000_000.0,
            return_12h_pct=5.5,
            return_4h_pct=3.0,
            return_15m_pct=0.6,
            taker_buy_4h_pct=54.0,
            taker_imbalance_15m=0.20,
            vol_zscore_15m=1.2,
            atr_15m_pct=0.9,
            beta_to_btc=1.8,
            residual_alpha_pct=4.1,
            is_liquid=True
        )

        snapshot = MarketSnapshot(
            timestamp=1000000.0,
            btc_above_ema20=True,
            btc_taker_buy_pct=52.0,
            btc_atr_normal=True,
            assets={"BTCUSDT": btc_metric, "ADAUSDT": ada_metric},
            exchange_info={}
        )

        decision = engine.evaluate(snapshot, portfolio_state={})
        self.assertEqual(decision.regime, "BULL_EXPANSION")
        self.assertEqual(decision.target_weights["BTC/USD"], 0.20)  # 20% Anchor Core
        self.assertIn("ADA/USD", decision.target_weights)
        self.assertEqual(decision.target_weights["ADA/USD"], 0.20)  # Capped at MAX_ALTCOIN_ALLOCATION (20%)
        self.assertGreaterEqual(decision.target_weights["USD"], 0.20)      # 20% Cash Buffer

    def test_decision_engine_4_tier_and_cross_crypto_lag(self):
        """
        Validates the 4-Tier Categorical Architecture:
        1. Core Anchor (BTC)
        2. Smart Contracts (SUI chosen over SOL due to positive Beta-Disparity Lag Spread)
        3. Infrastructure (LINK)
        4. Speculative Beta (DOGE)
        """
        engine = DecisionEngine()

        btc_metric = AssetMetrics(
            symbol="BTCUSDT", roostoo_pair="BTC/USD", last_price=85000.0, spread_pct=0.001,
            volume_24h_usd=1_000_000_000.0, return_12h_pct=2.5, return_4h_pct=2.0, return_15m_pct=0.2,
            taker_buy_4h_pct=52.0, taker_imbalance_15m=0.10, vol_zscore_15m=0.5, atr_15m_pct=0.4,
            beta_to_btc=1.0, residual_alpha_pct=0.0, is_liquid=True, lag_spread_4h_pct=0.0
        )

        # In Tier 2: SOL vs SUI. SUI has strong positive lag spread (lagging BTC breakout)
        sol_metric = AssetMetrics(
            symbol="SOLUSDT", roostoo_pair="SOL/USD", last_price=120.0, spread_pct=0.010,
            volume_24h_usd=80_000_000.0, return_12h_pct=3.0, return_4h_pct=3.0, return_15m_pct=0.1,
            taker_buy_4h_pct=52.0, taker_imbalance_15m=0.10, vol_zscore_15m=0.5, atr_15m_pct=0.7,
            beta_to_btc=1.2, residual_alpha_pct=0.5, is_liquid=True, lag_spread_4h_pct=-0.6
        )

        sui_metric = AssetMetrics(
            symbol="SUIUSDT", roostoo_pair="SUI/USD", last_price=2.10, spread_pct=0.012,
            volume_24h_usd=60_000_000.0, return_12h_pct=3.2, return_4h_pct=1.0, return_15m_pct=0.3,
            taker_buy_4h_pct=53.0, taker_imbalance_15m=0.15, vol_zscore_15m=0.8, atr_15m_pct=0.9,
            beta_to_btc=1.6, residual_alpha_pct=0.8, is_liquid=True, lag_spread_4h_pct=2.2  # Lagging BTC!
        )

        # Tier 3: LINK
        link_metric = AssetMetrics(
            symbol="LINKUSDT", roostoo_pair="LINK/USD", last_price=15.0, spread_pct=0.015,
            volume_24h_usd=40_000_000.0, return_12h_pct=2.8, return_4h_pct=1.5, return_15m_pct=0.2,
            taker_buy_4h_pct=52.5, taker_imbalance_15m=0.10, vol_zscore_15m=0.6, atr_15m_pct=0.8,
            beta_to_btc=1.3, residual_alpha_pct=0.6, is_liquid=True, lag_spread_4h_pct=1.1
        )

        # Tier 4: DOGE
        doge_metric = AssetMetrics(
            symbol="DOGEUSDT", roostoo_pair="DOGE/USD", last_price=0.14, spread_pct=0.018,
            volume_24h_usd=50_000_000.0, return_12h_pct=4.0, return_4h_pct=2.0, return_15m_pct=0.4,
            taker_buy_4h_pct=53.0, taker_imbalance_15m=0.12, vol_zscore_15m=0.7, atr_15m_pct=1.1,
            beta_to_btc=1.7, residual_alpha_pct=1.2, is_liquid=True, lag_spread_4h_pct=1.4
        )

        snapshot = MarketSnapshot(
            timestamp=1000000.0,
            btc_above_ema20=True,
            btc_taker_buy_pct=52.0,
            btc_atr_normal=True,
            assets={
                "BTCUSDT": btc_metric,
                "SOLUSDT": sol_metric,
                "SUIUSDT": sui_metric,
                "LINKUSDT": link_metric,
                "DOGEUSDT": doge_metric
            },
            exchange_info={}
        )

        decision = engine.evaluate(snapshot, portfolio_state={})
        self.assertEqual(decision.regime, "BULL_EXPANSION")
        
        # Verify Tier 1: BTC
        self.assertIn("BTC/USD", decision.target_weights)
        self.assertEqual(decision.target_weights["BTC/USD"], 0.20)
        
        # Verify Tier 2: SUI selected over SOL due to lag spread catch-up score
        self.assertIn("SUI/USD", decision.target_weights)
        self.assertNotIn("SOL/USD", decision.target_weights)
        
        # Verify Tier 3: LINK
        self.assertIn("LINK/USD", decision.target_weights)
        
        # Verify Tier 4: DOGE
        self.assertIn("DOGE/USD", decision.target_weights)
        
        # Verify Cash Buffer is preserved
        self.assertIn("USD", decision.target_weights)
        self.assertGreaterEqual(decision.target_weights["USD"], 0.20)

if __name__ == "__main__":
    unittest.main()
