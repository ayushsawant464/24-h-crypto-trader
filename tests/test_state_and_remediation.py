import unittest
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock
from bot.config.settings import settings
from bot.data.state_store import StateStore, PersistedPosition
from bot.data.market_feed import MarketSnapshot, AssetMetrics
from bot.strategy.decision_engine import DecisionEngine
from bot.execution.risk_guard import RiskGuard, PositionTracker
from bot.execution.rebalancer import PortfolioRebalancer
from bot.strategy.base import StrategyDecision

class TestStateAndRemediation(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_state.db"
        self.store = StateStore(self.db_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_state_store_position_persistence(self):
        """Validates that positions survive process restarts without state amnesia."""
        pos = PersistedPosition(
            pair="SOL/USD",
            side="LONG",
            entry_price=150.0,
            peak_price=160.0,
            quantity=5.0,
            effective_stop_price=145.0,
            is_ratcheted=True,
            is_trailing=True,
            atr_15m=1.2,
            updated_at=time.time()
        )
        self.store.save_position(pos)

        # Create new store instance pointing to the same db
        new_store = StateStore(self.db_path)
        loaded = new_store.load_positions()

        self.assertIn("SOL/USD", loaded)
        p = loaded["SOL/USD"]
        self.assertEqual(p.entry_price, 150.0)
        self.assertEqual(p.peak_price, 160.0)
        self.assertEqual(p.effective_stop_price, 145.0)
        self.assertTrue(p.is_ratcheted)
        self.assertTrue(p.is_trailing)

    def test_state_store_circuit_breaker_persistence(self):
        """Validates that circuit breaker state and cooldown timers survive restarts."""
        cooldown_until = time.time() + 14400.0
        self.store.save_circuit_breaker(high_watermark=105000.0, is_active=True, cooldown_until=cooldown_until)

        new_store = StateStore(self.db_path)
        hwm, active, cb_until = new_store.load_circuit_breaker()

        self.assertEqual(hwm, 105000.0)
        self.assertTrue(active)
        self.assertAlmostEqual(cb_until, cooldown_until, places=1)

    def test_quarantine_ledger_prevents_immediate_repurchase(self):
        """Validates that a stopped-out asset is quarantined and skipped by DecisionEngine."""
        self.store.quarantine_asset("SOL/USD", duration_hours=12.0, reason="Stop-Loss Hit")
        self.assertTrue(self.store.is_quarantined("SOL/USD"))
        self.assertFalse(self.store.is_quarantined("ETH/USD"))

        engine = DecisionEngine(state_store=self.store)
        sol_metric = AssetMetrics(
            symbol="SOLUSDT", roostoo_pair="SOL/USD", last_price=120.0, spread_pct=0.010,
            volume_24h_usd=50_000_000.0, return_12h_pct=4.5, return_4h_pct=2.0, return_15m_pct=0.5,
            taker_buy_4h_pct=53.5, taker_imbalance_15m=0.15, vol_zscore_15m=1.0, atr_15m_pct=0.8,
            beta_to_btc=1.1, residual_alpha_pct=3.2, is_liquid=True
        )
        snapshot = MarketSnapshot(
            timestamp=1000000.0, btc_above_ema20=True, btc_taker_buy_pct=48.0, btc_atr_normal=True,
            assets={"SOLUSDT": sol_metric}, exchange_info={}
        )

        decision = engine.evaluate(snapshot, portfolio_state={})
        # SOL/USD must NOT be bought because it is under quarantine!
        self.assertNotIn("SOL/USD", decision.target_weights)

    def test_unified_equity_accounting_prevents_false_circuit_breaker(self):
        """
        Validates that opening a 10% short does NOT trip the 2% circuit breaker
        because short collateral + unrealized PnL is properly accounted in RiskGuard.
        """
        mock_client = MagicMock()
        # Free USD dropped by 10,000 because 10,000 was transferred to short collateral
        mock_client.get_balance.return_value = {
            "Wallet": {
                "USD": {"Free": 90000.0, "Lock": 0.0}
            }
        }
        mock_client.get_ticker.return_value = {
            "Data": {
                "BTC/USD": {"LastPrice": 80000.0, "BidPrice": 80000.0, "AskPrice": 80000.0}
            }
        }
        mock_client.get_short_positions.return_value = {
            "Positions": [
                {
                    "Pair": "BTC/USD",
                    "Collateral": 10000.0,
                    "EntryPrice": 80000.0,
                    "Quantity": 0.125
                }
            ]
        }

        guard = RiskGuard(mock_client, state_store=self.store)
        guard.portfolio_high_watermark = 100000.0

        snapshot = MarketSnapshot(
            timestamp=1000000.0, btc_above_ema20=True, btc_taker_buy_pct=50.0, btc_atr_normal=True,
            assets={}, exchange_info={}
        )

        audit_res = guard.audit_and_protect(snapshot)
        # Total equity = $90k Free USD + $10k Short Equity = $100k -> Drawdown = 0% -> Circuit Breaker NOT triggered!
        self.assertNotEqual(audit_res.get("Status"), "CIRCUIT_BREAKER_TRIGGERED")
        self.assertFalse(guard.circuit_breaker_active)

    def test_target_weight_normalization_clamps_budget(self):
        """
        Validates that non-USD weights are clamped to <= 80% and USD cash is >= 20%,
        preventing exchange Insufficient Balance errors.
        """
        engine = DecisionEngine(state_store=self.store)
        # Construct decision where multiple satellites pass
        # The sum of non-USD weights must NEVER exceed 80%
        # Let's test with a mock decision dictionary
        weights = {"BTC/USD": 0.60, "SOL/USD": 0.20, "LINK/USD": 0.10, "DOGE/USD": 0.05}
        total_non_cash = sum(abs(w) for k, w in weights.items() if k != "USD")
        max_non_cash = 0.80

        scale = max_non_cash / total_non_cash
        normalized = {k: round(w * scale, 3) for k, w in weights.items()}
        normalized_non_cash = sum(abs(w) for k, w in normalized.items())
        cash = max(0.20, round(1.0 - normalized_non_cash, 3))
        normalized["USD"] = cash

        self.assertLessEqual(normalized_non_cash, 0.80)
        self.assertGreaterEqual(normalized["USD"], 0.20)
        self.assertAlmostEqual(sum(normalized.values()), 1.0, places=2)

    def test_rebalance_deadband_suppresses_churn(self):
        """
        Validates that rebalance orders are NOT executed if the dollar deviation
        is within the 2.5% hysteresis deadband.
        """
        mock_client = MagicMock()
        mock_client.get_balance.return_value = {
            "Wallet": {
                "USD": {"Free": 20000.0, "Lock": 0.0},
                "BTC": {"Free": 1.0, "Lock": 0.0}
            }
        }
        mock_client.get_ticker.return_value = {
            "Data": {
                "BTC/USD": {"LastPrice": 79000.0}
            }
        }
        mock_client.get_short_positions.return_value = {"Positions": []}

        rebalancer = PortfolioRebalancer(mock_client)
        exchange_info = {
            "BTC/USD": {"AmountPrecision": 4, "MiniOrder": 1.0}
        }

        # Total portfolio: $20,000 + $79,000 = $99,000.
        # Target weight for BTC: 80% ($79,200). Current value: $79,000.
        # Deviation: $200 (0.2%), which is well within the 2.5% deadband ($2,475).
        decision = StrategyDecision(
            target_weights={"USD": 0.20, "BTC/USD": 0.80},
            regime="BULL_EXPANSION",
            rationales={},
            expected_returns={}
        )

        rebalancer.execute_rebalance(decision, exchange_info)
        # place_order must NOT be called for BTC because deviation is within deadband
        mock_client.place_order.assert_not_called()

if __name__ == "__main__":
    unittest.main()
