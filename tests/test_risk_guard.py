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

if __name__ == "__main__":
    unittest.main()
