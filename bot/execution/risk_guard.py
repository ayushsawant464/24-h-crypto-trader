import time
from typing import Dict, Any, Optional
from dataclasses import dataclass
from bot.config.settings import settings
from bot.data.roostoo_client import RoostooClient
from bot.data.market_feed import MarketSnapshot
from bot.logs.logger import logger, log_trade

@dataclass
class PositionTracker:
    pair: str
    entry_price: float
    peak_price: float
    quantity: float
    effective_stop_price: float
    is_ratcheted: bool
    is_trailing: bool

class RiskGuard:
    """
    Real-time 1-minute risk daemon enforcing:
    - Volatility-Adjusted Hard Stop-Loss
    - Breakeven Profit Ratchet (+2.0% -> +0.4%)
    - Trailing Profit Lock (+4.0% -> Peak - 1.2%)
    - Order Flow Toxicity Emergency Exit
    - Portfolio Circuit Breaker (2.0% Drawdown -> 100% Cash)
    """
    def __init__(self, roostoo_client: RoostooClient):
        self.roostoo = roostoo_client
        self.active_positions: Dict[str, PositionTracker] = {}
        self.portfolio_high_watermark: float = 100000.0
        self.circuit_breaker_active: bool = False
        self.circuit_breaker_until: float = 0.0

    def update_positions_from_wallet(self, tickers: Dict[str, Any], exchange_info: Dict[str, Any]):
        """
        Synchronizes active position tracking with Roostoo wallet state.
        """
        balance_resp = self.roostoo.get_balance()
        wallet = balance_resp.get("Wallet", {})

        current_pairs = set()
        for coin, val in wallet.items():
            if coin == "USD":
                continue
            tot_qty = float(val.get("Free", 0.0)) + float(val.get("Lock", 0.0))
            pair = f"{coin}/USD"
            price = tickers.get(pair, {}).get("LastPrice", 0.0)

            # If position is non-trivial (> $25 value)
            if tot_qty * price > 25.0:
                current_pairs.add(pair)
                if pair not in self.active_positions:
                    # New position detected
                    stop_p = price * (1.0 - settings.HARD_STOP_LOSS_PCT / 100.0)
                    self.active_positions[pair] = PositionTracker(
                        pair=pair,
                        entry_price=price,
                        peak_price=price,
                        quantity=tot_qty,
                        effective_stop_price=stop_p,
                        is_ratcheted=False,
                        is_trailing=False
                    )
                    logger.info(f"[RISK GUARD] Tracking new position: {pair} @ ${price:,.4f} | Stop: ${stop_p:,.4f}")
                else:
                    # Update quantity and peak
                    pos = self.active_positions[pair]
                    pos.quantity = tot_qty
                    if price > pos.peak_price:
                        pos.peak_price = price

        # Remove closed positions
        for p in list(self.active_positions.keys()):
            if p not in current_pairs:
                logger.info(f"[RISK GUARD] Position closed: {p}")
                del self.active_positions[p]

    def audit_and_protect(self, snapshot: MarketSnapshot) -> Dict[str, Any]:
        """
        Executes real-time micro-loop risk audit.
        """
        now = time.time()
        
        # Check Circuit Breaker Cooldown
        if self.circuit_breaker_active:
            if now < self.circuit_breaker_until:
                logger.warning("[CIRCUIT BREAKER ACTIVE] All positions liquidated. In defensive cooldown.")
                return {"Status": "COOLDOWN", "RemainingSeconds": int(self.circuit_breaker_until - now)}
            else:
                self.circuit_breaker_active = False
                logger.info("[CIRCUIT BREAKER EXPIRED] Resuming standard operations.")

        ticker_resp = self.roostoo.get_ticker()
        tickers = ticker_resp.get("Data", {})

        # 1. Update High Watermark & Check Portfolio Circuit Breaker
        balance_resp = self.roostoo.get_balance()
        wallet = balance_resp.get("Wallet", {})
        total_val = float(wallet.get("USD", {}).get("Free", 0.0)) + float(wallet.get("USD", {}).get("Lock", 0.0))

        for coin, val in wallet.items():
            if coin != "USD":
                qty = float(val.get("Free", 0.0)) + float(val.get("Lock", 0.0))
                p = tickers.get(f"{coin}/USD", {}).get("LastPrice", 0.0)
                total_val += (qty * p)

        if total_val > self.portfolio_high_watermark:
            self.portfolio_high_watermark = total_val

        # Circuit Breaker Check
        drawdown_pct = (self.portfolio_high_watermark - total_val) / max(self.portfolio_high_watermark, 1.0) * 100
        if drawdown_pct >= settings.PORTFOLIO_CIRCUIT_BREAKER_PCT and total_val > 1000:
            logger.critical(
                f"[PORTFOLIO CIRCUIT BREAKER TRIGGERED] Drawdown: {drawdown_pct:.2f}% >= {settings.PORTFOLIO_CIRCUIT_BREAKER_PCT}%. "
                f"Emergency liquidation of 100% capital into USD Cash!"
            )
            self._emergency_liquidate_all(tickers, snapshot.exchange_info)
            self.circuit_breaker_active = True
            self.circuit_breaker_until = now + (settings.CIRCUIT_BREAKER_COOLDOWN_HOURS * 3600)
            return {"Status": "CIRCUIT_BREAKER_TRIGGERED", "Drawdown": drawdown_pct}

        # 2. Synchronize position state
        self.update_positions_from_wallet(tickers, snapshot.exchange_info)

        # 3. Audit Individual Open Positions
        for pair, pos in list(self.active_positions.items()):
            curr_price = tickers.get(pair, {}).get("LastPrice", 0.0)
            if curr_price <= 0:
                continue

            gain_pct = (curr_price - pos.entry_price) / pos.entry_price * 100.0

            # Update Peak
            if curr_price > pos.peak_price:
                pos.peak_price = curr_price

            # Check Stage 1: Breakeven Profit Ratchet (+2.0% -> +0.4%)
            if gain_pct >= settings.PROFIT_RATCHET_TRIGGER_PCT and not pos.is_ratcheted:
                new_stop = pos.entry_price * (1.0 + settings.PROFIT_RATCHET_LOCK_PCT / 100.0)
                if new_stop > pos.effective_stop_price:
                    pos.effective_stop_price = new_stop
                    pos.is_ratcheted = True
                    logger.info(
                        f"[PROFIT RATCHET] {pair} gained +{gain_pct:.2f}%. "
                        f"Stop ratcheted to Breakeven (+0.4% fees covered): ${new_stop:,.4f}"
                    )

            # Check Stage 2: Trailing Stop Lock (+4.0% -> Peak - 1.2%)
            if gain_pct >= settings.TRAILING_STOP_TRIGGER_PCT:
                pos.is_trailing = True
                trail_stop = pos.peak_price * (1.0 - settings.TRAILING_STOP_OFFSET_PCT / 100.0)
                if trail_stop > pos.effective_stop_price:
                    pos.effective_stop_price = trail_stop
                    logger.info(
                        f"[TRAILING STOP] {pair} peak ${pos.peak_price:,.4f}. "
                        f"Trailing stop ratcheted to ${trail_stop:,.4f}"
                    )

            # Check Order Flow Toxicity Emergency Exit
            sym = pair.replace("/USD", "USDT")
            metric = snapshot.assets.get(sym)
            if metric and metric.taker_imbalance_15m < -0.25 and metric.vol_zscore_15m > 2.5:
                logger.warning(
                    f"[TOXICITY EXIT] {pair} detected extreme order flow toxicity "
                    f"(Imbalance: {metric.taker_imbalance_15m:.2f}, Vol Z: {metric.vol_zscore_15m:.1f}). Emergency sell!"
                )
                self._execute_stop_sell(pair, pos, curr_price, snapshot.exchange_info, reason="Toxicity Emergency Exit")
                continue

            # Check Stop-Loss Execution
            if curr_price <= pos.effective_stop_price:
                loss_or_gain = "Stop-Loss" if curr_price < pos.entry_price else "Trailing Take-Profit"
                logger.warning(
                    f"[{loss_or_gain.upper()} TRIGGERED] {pair} Price: ${curr_price:,.4f} <= Stop: ${pos.effective_stop_price:,.4f}. "
                    f"Executing immediate market liquidation!"
                )
                self._execute_stop_sell(pair, pos, curr_price, snapshot.exchange_info, reason=loss_or_gain)

        return {"Status": "OK", "ActivePositions": len(self.active_positions)}

    def _execute_stop_sell(self, pair: str, pos: PositionTracker, price: float, exchange_info: Dict[str, Any], reason: str):
        amt_prec = exchange_info.get(pair, {}).get("AmountPrecision", 4)
        sell_qty = round(pos.quantity, amt_prec)
        resp = self.roostoo.place_order(pair=pair, side="SELL", quantity=sell_qty, order_type="MARKET")
        order_id = resp.get("OrderId", resp.get("Data", {}).get("OrderId", "mock_id"))
        pnl_pct = (price - pos.entry_price) / pos.entry_price * 100.0
        log_trade(
            symbol=pair,
            side="SELL",
            price=price,
            quantity=sell_qty,
            order_id=order_id,
            api_response=resp,
            signal_reason=f"{reason} | PnL: {pnl_pct:+.2f}%",
            pnl=round(pnl_pct, 2)
        )
        if pair in self.active_positions:
            del self.active_positions[pair]

    def _emergency_liquidate_all(self, tickers: Dict[str, Any], exchange_info: Dict[str, Any]):
        for pair, pos in list(self.active_positions.items()):
            curr_p = tickers.get(pair, {}).get("LastPrice", pos.entry_price)
            self._execute_stop_sell(pair, pos, curr_p, exchange_info, reason="Circuit Breaker Full Liquidation")
