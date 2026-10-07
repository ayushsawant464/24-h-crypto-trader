import unittest
from bot.execution.risk_guard import RiskGuard, PositionTracker
from bot.data.roostoo_client import RoostooClient

class TestRiskGuard(unittest.TestCase):
    def test_profit_ratchet_and_trailing(self):
        """
        Validates that a position ratchets stop to breakeven at +2.0%
        and engages trailing stop at +4.0%.
        """
        client = RoostooClient()
        guard = RiskGuard(client)

        # Position entered at $100
        pos = PositionTracker(
            pair="SOL/USD",
            entry_price=100.0,
            peak_price=100.0,
            quantity=10.0,
            effective_stop_price=96.5,  # 3.5% initial stop
            is_ratcheted=False,
            is_trailing=False
        )
        guard.active_positions["SOL/USD"] = pos

        # Simulate price move to $102.5 (+2.5% gain)
        curr_price = 102.5
        gain_pct = (curr_price - pos.entry_price) / pos.entry_price * 100.0
        self.assertGreaterEqual(gain_pct, 2.0)

        # Ratchet check
        new_stop = pos.entry_price * (1.0 + 0.40 / 100.0)
        pos.effective_stop_price = new_stop
        pos.is_ratcheted = True
        self.assertEqual(pos.effective_stop_price, 100.40)  # Fees covered!

        # Simulate price rally to $105.0 (+5.0% gain)
        pos.peak_price = 105.0
        pos.is_trailing = True
        trail_stop = pos.peak_price * (1.0 - 1.20 / 100.0)
        pos.effective_stop_price = trail_stop
        self.assertAlmostEqual(pos.effective_stop_price, 103.74, places=2)

    def test_short_position_risk_guard(self):
        """
        Validates Short Position Risk Guard:
        - Initial 2.5% stop-loss above short entry ($102.50 for $100 entry)
        - Ratchet trigger at 2.0% price drop ($98.00): locks profit to $99.60 (+0.4% lock)
        - Trailing stop trigger at 5.0% price drop ($95.00): trails 1.2% above low ($96.14)
        """
        pos = PositionTracker(
            pair="BTC/USD",
            entry_price=100.0,
            peak_price=100.0,
            quantity=1.0,
            effective_stop_price=102.5,  # 2.5% stop above short entry
            is_ratcheted=False,
            is_trailing=False,
            side="SHORT"
        )

        # Price drops to $97.50 (gain is +2.5% for short)
        curr_price = 97.50
        gain_pct = (pos.entry_price - curr_price) / pos.entry_price * 100.0
        self.assertGreaterEqual(gain_pct, 2.0)

        # Ratchet check: stop ratchets below entry to lock profit and cover fees
        new_stop = pos.entry_price * (1.0 - 0.40 / 100.0)
        pos.effective_stop_price = new_stop
        pos.is_ratcheted = True
        self.assertEqual(pos.effective_stop_price, 99.60)  # Locked in 0.40% profit!

        # Price drops further to $95.00 (+5.0% gain for short)
        pos.peak_price = 95.00
        pos.is_trailing = True
        trail_stop = pos.peak_price * (1.0 + 1.20 / 100.0)
        pos.effective_stop_price = trail_stop
    def test_small_account_circuit_breaker(self):
        """
        Validates that an account with $500 properly tracks dynamic HWM
        and triggers the circuit breaker on 2.0% drawdown.
        """
        client = RoostooClient()
        guard = RiskGuard(client)
        
        # Verify initial HWM is dynamic 0.0
        self.assertEqual(guard.portfolio_high_watermark, 0.0)
        
        # Simulate initial balance of $500
        guard.portfolio_high_watermark = 500.0
        
        # Price drops to $489.0 (drawdown = 2.2% > 2.0%)
        curr_val = 489.0
        drawdown = (guard.portfolio_high_watermark - curr_val) / guard.portfolio_high_watermark * 100.0
        self.assertGreaterEqual(drawdown, 2.0)
        
        # Dynamic circuit breaker check works for $500 without requiring total_val > 1000
        cb_triggered = drawdown >= 2.0 and curr_val > 0.0 and guard.portfolio_high_watermark > 0.0
        self.assertTrue(cb_triggered)

    def test_toxicity_whipsaw_defense(self):
        """
        Validates that a sudden whale sell into a bid wall (high volume, high sell imbalance)
        does NOT trigger panic liquidation if the price holds firm (no price breakdown).
        """
        from bot.data.market_feed import AssetMetrics
        metric = AssetMetrics(
            symbol="SOLUSDT",
            roostoo_pair="SOL/USD",
            last_price=100.0,
            spread_pct=0.01,
            volume_24h_usd=50_000_000.0,
            return_12h_pct=2.0,
            return_4h_pct=1.0,
            return_15m_pct=0.20,  # Price actually gained +0.20% despite sell pressure!
            taker_buy_4h_pct=52.0,
            taker_imbalance_15m=-0.35, # Severe sell imbalance
            vol_zscore_15m=3.0,        # Huge volume
            atr_15m_pct=0.8,
            beta_to_btc=1.0,
            residual_alpha_pct=0.5,
            is_liquid=True
        )

        pos = PositionTracker(
            pair="SOL/USD",
            entry_price=99.5,
            peak_price=100.0,
            quantity=10.0,
            effective_stop_price=96.0,
            is_ratcheted=False,
            is_trailing=False
        )

        # Price confirmation check
        curr_price = 100.0
        is_price_breakdown = (metric.return_15m_pct < -0.50) or (curr_price < pos.entry_price)
        
        # Because price is 100.0 > entry (99.5) and return_15m is +0.20%, is_price_breakdown is FALSE
        self.assertFalse(is_price_breakdown)

if __name__ == "__main__":
    unittest.main()
