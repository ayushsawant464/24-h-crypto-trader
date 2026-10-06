import unittest
from bot.strategy.decision_engine import DecisionEngine
from bot.data.market_feed import MarketSnapshot, AssetMetrics

class TestDecisionEngine(unittest.TestCase):
    def test_decision_engine_macro_filter(self):
        """
        Validates that when BTC drops below EMA20, Gate 1 halts long allocations
        and moves 100% to Cash Bunker (minimum 0 assets in worst condition).
        """
        engine = DecisionEngine()
        
        # Snapshot where BTC is below EMA20
        snapshot = MarketSnapshot(
            timestamp=1000000.0,
            btc_above_ema20=False,
            btc_taker_buy_pct=42.0,
            btc_atr_normal=True,
            assets={},
            exchange_info={}
        )

        decision = engine.evaluate(snapshot, portfolio_state={})
        self.assertEqual(decision.regime, "CASH_BUNKER")
        self.assertEqual(decision.target_weights, {"USD": 1.0})

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
        self.assertGreaterEqual(decision.target_weights["ADA/USD"], 0.30)  # Overweighted small-cap
        self.assertGreaterEqual(decision.target_weights["USD"], 0.20)      # 20% Cash Buffer

if __name__ == "__main__":
    unittest.main()
