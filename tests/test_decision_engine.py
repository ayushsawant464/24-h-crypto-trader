import unittest
from bot.strategy.decision_engine import DecisionEngine
from bot.data.market_feed import MarketSnapshot, AssetMetrics

class TestDecisionEngine(unittest.TestCase):
    def test_decision_engine_macro_filter(self):
        """
        Validates that when BTC drops below EMA20, Gate 1 halts long allocations
        and moves 100% to Cash Bunker.
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

    def test_decision_engine_alpha_selection(self):
        """
        Validates that candidate asset passing all 5 gates receives positive allocation.
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
        self.assertEqual(decision.regime, "BULL_MOMENTUM_ALPHA")
        self.assertIn("SOL/USD", decision.target_weights)
        self.assertGreater(decision.target_weights["SOL/USD"], 0.10)
        self.assertGreaterEqual(decision.target_weights["USD"], 0.20)  # Minimum cash buffer preserved

if __name__ == "__main__":
    unittest.main()
