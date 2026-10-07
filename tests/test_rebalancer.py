import unittest
from unittest.mock import MagicMock
from bot.execution.rebalancer import PortfolioRebalancer, floor_to_precision
from bot.strategy.base import StrategyDecision

class TestRebalancer(unittest.TestCase):
    def test_floor_to_precision(self):
        """Validates that floor_to_precision never rounds up."""
        self.assertEqual(floor_to_precision(1.23456, 3), 1.234)
        self.assertEqual(floor_to_precision(1.23499, 3), 1.234)
        self.assertEqual(floor_to_precision(10.9999, 2), 10.99)
        self.assertEqual(floor_to_precision(5.0, 4), 5.0)

    def test_rebalancer_locked_balance_bounding(self):
        """
        Validates that sell orders are strictly bounded by Free balance
        even when Total holding is higher due to locked funds.
        """
        mock_client = MagicMock()
        # Mock wallet where 10 SOL are held, but 4 SOL are locked (6 SOL free)
        mock_client.get_balance.return_value = {
            "Wallet": {
                "USD": {"Free": 1000.0, "Lock": 0.0},
                "SOL": {"Free": 6.0, "Lock": 4.0}
            }
        }
        mock_client.get_ticker.return_value = {
            "Data": {
                "SOL/USD": {"LastPrice": 100.0}
            }
        }
        mock_client.get_short_positions.return_value = {"Positions": []}
        mock_client.place_order.return_value = {"OrderId": "test_order_1"}

        rebalancer = PortfolioRebalancer(mock_client)
        exchange_info = {
            "SOL/USD": {"AmountPrecision": 2, "MiniOrder": 1.0}
        }

        # Target 0% SOL (liquidate position)
        decision = StrategyDecision(
            target_weights={"USD": 1.0, "SOL/USD": 0.0},
            regime="BEAR_CONTRACTION",
            rationales={},
            expected_returns={}
        )

        rebalancer.execute_rebalance(decision, exchange_info)

        # Place order must have been called with quantity <= 6.0 (the Free balance!), NOT 10.0!
        mock_client.place_order.assert_called()
        call_kwargs = mock_client.place_order.call_args.kwargs
        self.assertEqual(call_kwargs["pair"], "SOL/USD")
        self.assertEqual(call_kwargs["side"], "SELL")
        self.assertLessEqual(call_kwargs["quantity"], 6.0)

    def test_short_scale_down(self):
        """
        Validates that when target short weight is reduced from -20% to -10%,
        the rebalancer executes a partial short_close rather than leaving it over-leveraged.
        """
        mock_client = MagicMock()
        mock_client.get_balance.return_value = {
            "Wallet": {
                "USD": {"Free": 10000.0, "Lock": 0.0}
            }
        }
        mock_client.get_ticker.return_value = {
            "Data": {
                "BTC/USD": {"LastPrice": 80000.0}
            }
        }
        # Existing short position has $2,000 collateral (20% of portfolio)
        mock_client.get_short_positions.return_value = {
            "Positions": [
                {"Pair": "BTC/USD", "Collateral": 2000.0, "Quantity": 0.025}
            ]
        }
        mock_client.short_close.return_value = {"Success": True}

        rebalancer = PortfolioRebalancer(mock_client)
        exchange_info = {
            "BTC/USD": {"AmountPrecision": 4, "MiniOrder": 1.0}
        }

        # Total portfolio equity = $10,000 Free USD + $2,000 Short Collateral = $12,000
        # Target -10% short = $1,200 target collateral (vs $2,000 existing)
        # Reduction delta = $800 ($800 / $2,000 = 40.0% close)
        decision = StrategyDecision(
            target_weights={"USD": 0.90, "BTC/USD": -0.10},
            regime="BEAR_CONTRACTION",
            rationales={"BTC/USD": "Reduced short hedge"},
            expected_returns={}
        )

        rebalancer.execute_rebalance(decision, exchange_info)

        # Must have called short_close to scale down short by 40% ($800 reduction / $2,000 existing)
        mock_client.short_close.assert_called()
        call_kwargs = mock_client.short_close.call_args.kwargs
        self.assertEqual(call_kwargs["pair"], "BTC/USD")
        self.assertAlmostEqual(call_kwargs["close_pct"], 40.0, places=1)

    def test_spot_long_liquidated_when_switching_to_short(self):
        """
        Validates that when target weight switches to negative (short hedge, target_w = -0.10),
        any existing spot long is 100% liquidated before opening the short.
        """
        mock_client = MagicMock()
        # Holding 0.5 BTC spot in wallet
        mock_client.get_balance.return_value = {
            "Wallet": {
                "USD": {"Free": 10000.0, "Lock": 0.0},
                "BTC": {"Free": 0.5, "Lock": 0.0}
            }
        }
        mock_client.get_ticker.return_value = {
            "Data": {
                "BTC/USD": {"LastPrice": 80000.0}
            }
        }
        mock_client.get_short_positions.return_value = {"Positions": []}
        mock_client.place_order.return_value = {"OrderId": "liquidate_btc_spot"}
        mock_client.short_open.return_value = {"OrderId": "open_btc_short"}

        rebalancer = PortfolioRebalancer(mock_client)
        exchange_info = {
            "BTC/USD": {"AmountPrecision": 4, "MiniOrder": 1.0}
        }

        # Target is -10% short BTC
        decision = StrategyDecision(
            target_weights={"USD": 0.90, "BTC/USD": -0.10},
            regime="BEAR_CONTRACTION",
            rationales={"BTC/USD": "Bear hedge"},
            expected_returns={}
        )

        rebalancer.execute_rebalance(decision, exchange_info)

        # Must have called place_order with SELL for 0.5 BTC
        mock_client.place_order.assert_called()
        call_kwargs = mock_client.place_order.call_args.kwargs
        self.assertEqual(call_kwargs["pair"], "BTC/USD")
        self.assertEqual(call_kwargs["side"], "SELL")
        self.assertEqual(call_kwargs["quantity"], 0.5)

if __name__ == "__main__":
    unittest.main()
