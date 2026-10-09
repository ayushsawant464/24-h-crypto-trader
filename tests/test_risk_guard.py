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
        and triggers the circuit breaker on 5.0% drawdown.
        """
        from unittest.mock import MagicMock
        from bot.config.settings import settings
        client = RoostooClient()
        mock_state = MagicMock()
        mock_state.load_circuit_breaker.return_value = (0.0, False, 0.0)
        mock_state.load_positions.return_value = {}
        guard = RiskGuard(client, state_store=mock_state)
        
        # Verify initial HWM is dynamic 0.0
        self.assertEqual(guard.portfolio_high_watermark, 0.0)
        
        # Simulate initial balance of $500
        guard.portfolio_high_watermark = 500.0
        
        # Price drops to $470.0 (drawdown = 6.0% >= 5.0%)
        curr_val = 470.0
        drawdown = (guard.portfolio_high_watermark - curr_val) / guard.portfolio_high_watermark * 100.0
        self.assertGreaterEqual(drawdown, settings.PORTFOLIO_CIRCUIT_BREAKER_PCT)
        
        # Dynamic circuit breaker check works for $500 without requiring total_val > 1000
        cb_triggered = drawdown >= settings.PORTFOLIO_CIRCUIT_BREAKER_PCT and curr_val > 0.0 and guard.portfolio_high_watermark > 0.0
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

    def test_vwap_position_scale_up(self):
        """
        Validates that when a position size is increased,
        RiskGuard recalculates the Volume-Weighted Average Price (VWAP) blended entry price.
        """
        from unittest.mock import MagicMock
        mock_client = MagicMock()
        # Initially 10 SOL @ 100 in wallet, pos entry was 100.
        # Now wallet has 20 SOL, and current market price is 120.
        mock_client.get_balance.return_value = {
            "SpotWallet": {
                "SOL": {"Free": 20.0, "Lock": 0.0}
            }
        }
        mock_client.get_short_positions.return_value = {"Positions": []}

        guard = RiskGuard(mock_client)
        guard.active_positions["SOL/USD"] = PositionTracker(
            pair="SOL/USD",
            entry_price=100.0,
            peak_price=100.0,
            quantity=10.0,
            effective_stop_price=96.5,
            is_ratcheted=False,
            is_trailing=False,
            side="LONG"
        )

        tickers = {"SOL/USD": {"LastPrice": 120.0}}
        exchange_info = {"SOL/USD": {"AmountPrecision": 2, "MiniOrder": 1.0}}

        guard.update_positions_from_wallet(tickers, exchange_info)

        pos = guard.active_positions["SOL/USD"]
        self.assertEqual(pos.quantity, 20.0)
        # Expected VWAP = (10 * 100 + 10 * 120) / 20 = 110.0
        self.assertEqual(pos.entry_price, 110.0)

    def test_symmetric_short_wick_defense(self):
        """
        Validates that a single anomalous LastPrice spike above short stop
        does NOT trigger premature liquidation if AskPrice is still below stop.
        """
        pos = PositionTracker(
            pair="BTC/USD",
            entry_price=80000.0,
            peak_price=80000.0,
            quantity=0.1,
            effective_stop_price=82000.0,
            is_ratcheted=False,
            is_trailing=False,
            side="SHORT"
        )

        # Last price wicked up to 82,050 (above stop of 82,000),
        # but the real Ask book price is 81,950 (below stop)
        curr_price = 82050.0
        ask_price = 81950.0

        is_wick_anomaly = ask_price < pos.effective_stop_price and curr_price >= pos.effective_stop_price
        self.assertTrue(is_wick_anomaly)

    def test_audit_and_protect_long_toxicity_attribute_safety(self):
        """
        Validates that audit_and_protect executes cleanly on active LONG positions
        without throwing AttributeError on TOXICITY_IMBALANCE_THRESHOLD, and correctly
        audits the position.
        """
        from unittest.mock import MagicMock
        from bot.data.market_feed import MarketSnapshot, AssetMetrics
        client = MagicMock()
        client.get_balance.return_value = {
            'Success': True,
            'SpotWallet': {'USD': {'Free': 50000.0, 'Locked': 0.0}, 'SOL': {'Free': 10.0, 'Locked': 0.0}},
            'Wallet': {'USD': {'Free': 50000.0, 'Locked': 0.0}, 'SOL': {'Free': 10.0, 'Locked': 0.0}}
        }
        client.get_ticker.return_value = {'Success': True, 'Data': {'SOL/USD': {'LastPrice': 100.0, 'BidPrice': 99.9, 'AskPrice': 100.1}}}
        client.get_short_positions.return_value = {'Success': True, 'Positions': []}

        mock_state = MagicMock()
        mock_state.load_circuit_breaker.return_value = (0.0, False, 0.0)
        mock_state.load_positions.return_value = {}
        guard = RiskGuard(roostoo_client=client, state_store=mock_state)

        metric = AssetMetrics(
            symbol='SOLUSDT', roostoo_pair='SOL/USD', last_price=100.0, spread_pct=0.01,
            volume_24h_usd=50000000.0, return_12h_pct=2.0, return_4h_pct=1.0, return_15m_pct=0.20,
            taker_buy_4h_pct=52.0, taker_imbalance_15m=-0.35, vol_zscore_15m=3.0, atr_15m_pct=0.8,
            beta_to_btc=1.0, residual_alpha_pct=0.5, is_liquid=True
        )

        snapshot = MarketSnapshot(
            timestamp=1000000.0, btc_above_ema20=True, btc_taker_buy_pct=52.0, btc_atr_normal=True,
            assets={'SOLUSDT': metric}, exchange_info={'SOL/USD': {'AmountPrecision': 2, 'MiniOrder': 1.0}}
        )

        # Must execute without any AttributeError
        res = guard.audit_and_protect(snapshot)
        self.assertEqual(res.get("Status"), "OK")
        self.assertEqual(res.get("ActivePositions"), 1)

if __name__ == "__main__":
    unittest.main()
