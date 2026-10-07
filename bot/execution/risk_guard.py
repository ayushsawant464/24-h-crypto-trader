import math
import time
from typing import Dict, Any, Optional
from dataclasses import dataclass
from bot.config.settings import settings
from bot.data.roostoo_client import RoostooClient
from bot.data.market_feed import MarketSnapshot
from bot.logs.logger import logger, log_trade

def floor_to_precision(val: float, precision: int) -> float:
    """Floors quantity down to precision to prevent Insufficient Balance errors."""
    if precision <= 0:
        return float(math.floor(val))
    factor = 10 ** precision
    return math.floor(val * factor) / factor

@dataclass
class PositionTracker:
    pair: str
    entry_price: float
    peak_price: float
    quantity: float
    effective_stop_price: float
    is_ratcheted: bool
    is_trailing: bool
    side: str = "LONG"

class RiskGuard:
    """
    Real-time 1-minute risk daemon enforcing:
    - Volatility-Adjusted Hard Stop-Loss (Long & Short)
    - Breakeven Profit Ratchet (+2.0% -> +0.4% fees covered)
    - Trailing Profit Lock (+4.0% -> Peak - 1.2%)
    - Order Flow Toxicity Emergency Exit / Squeeze Avoidance
    - Portfolio Circuit Breaker (2.0% Drawdown -> 100% Cash)
    """
    def __init__(self, roostoo_client: RoostooClient):
        self.roostoo = roostoo_client
        self.active_positions: Dict[str, PositionTracker] = {}
        self.portfolio_high_watermark: float = 0.0  # Initialized dynamically on first audit
        self.circuit_breaker_active: bool = False
        self.circuit_breaker_until: float = 0.0

    def update_positions_from_wallet(self, tickers: Dict[str, Any], exchange_info: Dict[str, Any]):
        """
        Synchronizes active position tracking with Roostoo wallet and short positions.
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

            # If spot position is non-trivial (> $25 value)
            if tot_qty * price > 25.0:
                current_pairs.add(pair)
                if pair not in self.active_positions:
                    # New spot long position detected
                    stop_p = price * (1.0 - settings.HARD_STOP_LOSS_PCT / 100.0)
                    self.active_positions[pair] = PositionTracker(
                        pair=pair,
                        entry_price=price,
                        peak_price=price,
                        quantity=tot_qty,
                        effective_stop_price=stop_p,
                        is_ratcheted=False,
                        is_trailing=False,
                        side="LONG"
                    )
                    logger.info(f"[RISK GUARD] Tracking new LONG position: {pair} @ ${price:,.4f} | Stop: ${stop_p:,.4f}")
                else:
                    # Update quantity and peak
                    pos = self.active_positions[pair]
                    pos.quantity = tot_qty
                    if price > pos.peak_price and pos.side == "LONG":
                        pos.peak_price = price

        # Also synchronize active short positions from /v6/short_positions
        try:
            short_resp = self.roostoo.get_short_positions()
            short_positions = short_resp.get("Positions", short_resp.get("Data", []))
            if isinstance(short_positions, list):
                for spos in short_positions:
                    spair = spos.get("Pair", spos.get("pair", ""))
                    if not spair:
                        continue
                    key = f"SHORT_{spair}"
                    current_pairs.add(key)
                    sentry = float(spos.get("EntryPrice", spos.get("entry_price", tickers.get(spair, {}).get("LastPrice", 0.0))))
                    sqty = float(spos.get("Quantity", spos.get("quantity", 0.0)))
                    sprice = tickers.get(spair, {}).get("LastPrice", sentry)

                    if key not in self.active_positions and sentry > 0:
                        stop_p = sentry * (1.0 + settings.SHORT_STOP_LOSS_PCT / 100.0)
                        self.active_positions[key] = PositionTracker(
                            pair=spair,
                            entry_price=sentry,
                            peak_price=sprice,
                            quantity=sqty,
                            effective_stop_price=stop_p,
                            is_ratcheted=False,
                            is_trailing=False,
                            side="SHORT"
                        )
                        logger.info(f"[RISK GUARD] Tracking new SHORT position: {spair} @ ${sentry:,.4f} | Stop: ${stop_p:,.4f}")
                    elif key in self.active_positions:
                        pos = self.active_positions[key]
                        pos.quantity = sqty
                        if sprice < pos.peak_price:  # Track lowest price for short
                            pos.peak_price = sprice
        except Exception as e:
            logger.debug(f"Note: Error checking short positions in risk guard: {e}")

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
                # CRITICAL FIX: Reset high watermark to current portfolio value after cooldown.
                # Without this, the drawdown from the old HWM would immediately re-trigger
                # the circuit breaker in an infinite 4-hour death spiral.
                ticker_resp_reset = self.roostoo.get_ticker()
                tickers_reset = ticker_resp_reset.get("Data", {})
                bal_reset = self.roostoo.get_balance()
                wallet_reset = bal_reset.get("Wallet", {})
                reset_val = float(wallet_reset.get("USD", {}).get("Free", 0.0)) + float(wallet_reset.get("USD", {}).get("Lock", 0.0))
                for coin, val in wallet_reset.items():
                    if coin != "USD":
                        qty = float(val.get("Free", 0.0)) + float(val.get("Lock", 0.0))
                        p = tickers_reset.get(f"{coin}/USD", {}).get("LastPrice", 0.0)
                        reset_val += (qty * p)
                self.portfolio_high_watermark = reset_val
                logger.info(f"[CIRCUIT BREAKER EXPIRED] Resuming operations. HWM reset to ${reset_val:,.2f}")

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

        if self.portfolio_high_watermark <= 0.0 and total_val > 0.0:
            self.portfolio_high_watermark = total_val
        elif total_val > self.portfolio_high_watermark:
            self.portfolio_high_watermark = total_val

        # Circuit Breaker Check (dynamic calibration for any portfolio size)
        drawdown_pct = ((self.portfolio_high_watermark - total_val) / self.portfolio_high_watermark * 100.0) if self.portfolio_high_watermark > 0.0 else 0.0
        if drawdown_pct >= settings.PORTFOLIO_CIRCUIT_BREAKER_PCT and total_val > 0.0 and self.portfolio_high_watermark > 0.0:
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

        # 3. Audit Individual Open Positions (Long and Short)
        for key, pos in list(self.active_positions.items()):
            curr_price = tickers.get(pos.pair, {}).get("LastPrice", 0.0)
            if curr_price <= 0:
                continue

            sym = pos.pair.replace("/USD", "USDT")
            metric = snapshot.assets.get(sym)

            # ==========================================
            # AUDIT CASE A: SPOT LONG POSITION
            # ==========================================
            if pos.side == "LONG":
                gain_pct = (curr_price - pos.entry_price) / pos.entry_price * 100.0
                if curr_price > pos.peak_price:
                    pos.peak_price = curr_price

                # Check Stage 1: Breakeven Profit Ratchet (+2.0% -> +0.4%)
                if gain_pct >= settings.PROFIT_RATCHET_TRIGGER_PCT and not pos.is_ratcheted:
                    new_stop = pos.entry_price * (1.0 + settings.PROFIT_RATCHET_LOCK_PCT / 100.0)
                    if new_stop > pos.effective_stop_price:
                        pos.effective_stop_price = new_stop
                        pos.is_ratcheted = True
                        logger.info(
                            f"[PROFIT RATCHET LONG] {pos.pair} gained +{gain_pct:.2f}%. "
                            f"Stop ratcheted to Breakeven (+0.4% fees covered): ${new_stop:,.4f}"
                        )

                # Check Stage 2: Trailing Stop Lock (+4.0% -> Peak - 1.2%)
                if gain_pct >= settings.TRAILING_STOP_TRIGGER_PCT:
                    pos.is_trailing = True
                    trail_stop = pos.peak_price * (1.0 - settings.TRAILING_STOP_OFFSET_PCT / 100.0)
                    if trail_stop > pos.effective_stop_price:
                        pos.effective_stop_price = trail_stop
                        logger.info(
                            f"[TRAILING STOP LONG] {pos.pair} peak ${pos.peak_price:,.4f}. "
                            f"Trailing stop ratcheted to ${trail_stop:,.4f}"
                        )

                # Check Order Flow Toxicity Emergency Exit with Price Confirmation (Anti Whip-Saw)
                if metric and metric.taker_imbalance_15m < settings.TOXICITY_IMBALANCE_THRESHOLD and metric.vol_zscore_15m > 2.5:
                    is_price_breakdown = (metric.return_15m_pct < -settings.TOXICITY_PRICE_DROP_CONFIRMATION_PCT) or (curr_price < pos.entry_price)
                    if is_price_breakdown:
                        logger.warning(
                            f"[TOXICITY EXIT CONFIRMED] {pos.pair} detected toxic dump with price breakdown "
                            f"(Imbalance: {metric.taker_imbalance_15m:.2f}, Vol Z: {metric.vol_zscore_15m:.1f}, 15m Ret: {metric.return_15m_pct:.2f}%). Emergency sell!"
                        )
                        self._execute_stop_sell(pos.pair, pos, curr_price, snapshot.exchange_info, reason="Toxicity Breakdown Emergency Exit")
                        continue
                    else:
                        logger.info(
                            f"[TOXICITY ABSORPTION] {pos.pair} high sell imbalance ({metric.taker_imbalance_15m:.2f}) "
                            f"absorbed by bid wall (15m Ret: {metric.return_15m_pct:+.2f}%). Avoiding whip-saw panic exit."
                        )

                # Check Early Momentum Decay Exit (prevents riding failing tokens into full 3.5% stop)
                if metric and metric.return_4h_pct < -1.5 and metric.taker_buy_4h_pct < 46.0:
                    if curr_price < pos.entry_price * (1.0 - settings.EARLY_MOMENTUM_DECAY_PCT / 100.0):
                        logger.warning(
                            f"[EARLY MOMENTUM DECAY EXIT] {pos.pair} momentum decaying (4h Ret: {metric.return_4h_pct:+.2f}%, "
                            f"Taker Buy: {metric.taker_buy_4h_pct:.1f}%). Cutting position early to protect capital!"
                        )
                        self._execute_stop_sell(pos.pair, pos, curr_price, snapshot.exchange_info, reason="Early Momentum Decay Cut")
                        continue

                # Check Stop-Loss Execution
                if curr_price <= pos.effective_stop_price:
                    loss_or_gain = "Stop-Loss" if curr_price < pos.entry_price else "Trailing Take-Profit"
                    logger.warning(
                        f"[{loss_or_gain.upper()} TRIGGERED] {pos.pair} Price: ${curr_price:,.4f} <= Stop: ${pos.effective_stop_price:,.4f}. "
                        f"Executing immediate market liquidation!"
                    )
                    self._execute_stop_sell(pos.pair, pos, curr_price, snapshot.exchange_info, reason=loss_or_gain)

            # ==========================================
            # AUDIT CASE B: SHORT POSITION
            # ==========================================
            elif pos.side == "SHORT":
                # For a short, gain increases as price falls
                gain_pct = (pos.entry_price - curr_price) / pos.entry_price * 100.0
                if curr_price < pos.peak_price:
                    pos.peak_price = curr_price  # Track lowest price achieved

                # Check Stage 1: Breakeven Profit Ratchet for Short (+2.0% drop -> lock +0.4%)
                if gain_pct >= settings.PROFIT_RATCHET_TRIGGER_PCT and not pos.is_ratcheted:
                    new_stop = pos.entry_price * (1.0 - settings.PROFIT_RATCHET_LOCK_PCT / 100.0)
                    if new_stop < pos.effective_stop_price:
                        pos.effective_stop_price = new_stop
                        pos.is_ratcheted = True
                        logger.info(
                            f"[PROFIT RATCHET SHORT] {pos.pair} price dropped {gain_pct:.2f}%. "
                            f"Short stop ratcheted to lock profit (+0.4% fees covered): ${new_stop:,.4f}"
                        )

                # Check Stage 2: Trailing Stop Lock for Short (+4.0% drop -> trail 1.2% above low)
                if gain_pct >= settings.TRAILING_STOP_TRIGGER_PCT:
                    pos.is_trailing = True
                    trail_stop = pos.peak_price * (1.0 + settings.TRAILING_STOP_OFFSET_PCT / 100.0)
                    if trail_stop < pos.effective_stop_price:
                        pos.effective_stop_price = trail_stop
                        logger.info(
                            f"[TRAILING STOP SHORT] {pos.pair} reached low ${pos.peak_price:,.4f}. "
                            f"Short trailing stop ratcheted to ${trail_stop:,.4f}"
                        )

                # Check Short Squeeze Toxicity Exit with Price Confirmation
                if metric and metric.taker_imbalance_15m > 0.25 and metric.vol_zscore_15m > 2.5:
                    is_squeeze_spike = (metric.return_15m_pct > settings.TOXICITY_PRICE_DROP_CONFIRMATION_PCT) or (curr_price > pos.entry_price)
                    if is_squeeze_spike:
                        logger.warning(
                            f"[SHORT SQUEEZE EXIT CONFIRMED] {pos.pair} detected aggressive buying volume and price breakout "
                            f"(Imbalance: {metric.taker_imbalance_15m:+.2f}, Vol Z: {metric.vol_zscore_15m:.1f}, 15m Ret: {metric.return_15m_pct:+.2f}%). Emergency closing short!"
                        )
                        self._execute_short_close(pos.pair, pos, curr_price, reason="Short Squeeze Emergency Exit")
                        continue
                    else:
                        logger.info(f"[SHORT SQUEEZE RESISTANCE] {pos.pair} buyer spike absorbed by ask wall. Holding short.")

                # Check Short Stop-Loss Execution (Price rallies above stop)
                if curr_price >= pos.effective_stop_price:
                    loss_or_gain = "Short Stop-Loss" if curr_price > pos.entry_price else "Short Trailing Take-Profit"
                    logger.warning(
                        f"[{loss_or_gain.upper()} TRIGGERED] {pos.pair} Price: ${curr_price:,.4f} >= Stop: ${pos.effective_stop_price:,.4f}. "
                        f"Executing immediate short position close!"
                    )
                    self._execute_short_close(pos.pair, pos, curr_price, reason=loss_or_gain)

        return {"Status": "OK", "ActivePositions": len(self.active_positions)}

    def _execute_stop_sell(self, pair: str, pos: PositionTracker, price: float, exchange_info: Dict[str, Any], reason: str):
        amt_prec = exchange_info.get(pair, {}).get("AmountPrecision", 4)
        mini_order = exchange_info.get(pair, {}).get("MiniOrder", 1.0)
        
        # Verify strictly available free wallet balance to avoid Insufficient Balance error
        curr_free = pos.quantity
        try:
            b_resp = self.roostoo.get_balance()
            c_coin = pair.split("/")[0]
            curr_free = float(b_resp.get("Wallet", {}).get(c_coin, {}).get("Free", pos.quantity))
            if curr_free < pos.quantity * 0.95:  # Balance locked by pending order
                self.roostoo.cancel_order(pair=pair)
                time.sleep(0.2)
                b_resp = self.roostoo.get_balance()
                curr_free = float(b_resp.get("Wallet", {}).get(c_coin, {}).get("Free", curr_free))
        except Exception as e:
            logger.debug(f"Note: Error checking free balance for stop sell on {pair}: {e}")

        sell_qty = min(curr_free, floor_to_precision(pos.quantity, amt_prec))
        if sell_qty * price >= mini_order and sell_qty > 0:
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

    def _execute_short_close(self, pair: str, pos: PositionTracker, price: float, reason: str):
        resp = self.roostoo.short_close(pair=pair, close_pct=100.0)
        order_id = resp.get("OrderId", resp.get("Data", {}).get("OrderId", "mock_short_close_id"))
        pnl_pct = (pos.entry_price - price) / pos.entry_price * 100.0
        log_trade(
            symbol=pair,
            side="SHORT_CLOSE",
            price=price,
            quantity=pos.quantity,
            order_id=order_id,
            api_response=resp,
            signal_reason=f"{reason} | Short PnL: {pnl_pct:+.2f}%",
            pnl=round(pnl_pct, 2)
        )
        key = f"SHORT_{pair}" if f"SHORT_{pair}" in self.active_positions else pair
        if key in self.active_positions:
            del self.active_positions[key]

    def _emergency_liquidate_all(self, tickers: Dict[str, Any], exchange_info: Dict[str, Any]):
        for key, pos in list(self.active_positions.items()):
            curr_p = tickers.get(pos.pair, {}).get("LastPrice", pos.entry_price)
            if pos.side == "SHORT":
                self._execute_short_close(pos.pair, pos, curr_p, reason="Circuit Breaker Short Liquidation")
            else:
                self._execute_stop_sell(pos.pair, pos, curr_p, exchange_info, reason="Circuit Breaker Full Liquidation")
