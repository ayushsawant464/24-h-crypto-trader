from decimal import Decimal, ROUND_DOWN
import math
import time
import concurrent.futures
from typing import Dict, Any, Optional
from dataclasses import dataclass
from bot.config.settings import settings
from bot.data.roostoo_client import RoostooClient
from bot.data.market_feed import MarketSnapshot
from bot.logs.logger import logger, log_trade

def floor_to_precision(val: float, precision: int) -> float:
    """Decimal-safe truncation down to precision to eliminate floating-point representation bugs."""
    if val <= 0:
        return 0.0
    if precision <= 0:
        return float(int(Decimal(str(val)).quantize(Decimal('1'), rounding=ROUND_DOWN)))
    q = Decimal('10') ** -precision
    d_val = Decimal(str(val)).quantize(q, rounding=ROUND_DOWN)
    return float(d_val)

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
    - State Persistence & Anti-Amnesia across restarts
    """
    def __init__(self, roostoo_client: RoostooClient, state_store: Optional[Any] = None):
        self.roostoo = roostoo_client
        self.active_positions: Dict[str, PositionTracker] = {}
        self.portfolio_high_watermark: float = 0.0  # Initialized dynamically on first audit
        self.circuit_breaker_active: bool = False
        self.circuit_breaker_until: float = 0.0

        if state_store is None:
            try:
                from bot.data.state_store import StateStore
                self.state_store = StateStore()
            except Exception:
                self.state_store = None
        else:
            self.state_store = state_store

        # Restore circuit breaker state if persisted
        if self.state_store:
            try:
                hwm, cb_active, cb_until = self.state_store.load_circuit_breaker()
                if hwm > 0:
                    self.portfolio_high_watermark = hwm
                self.circuit_breaker_active = cb_active
                self.circuit_breaker_until = cb_until

                # Restore persisted positions to eliminate stop-loss drift
                persisted = self.state_store.load_positions()
                for p_pair, p_pos in persisted.items():
                    self.active_positions[p_pair] = PositionTracker(
                        pair=p_pos.pair,
                        entry_price=p_pos.entry_price,
                        peak_price=p_pos.peak_price,
                        quantity=p_pos.quantity,
                        effective_stop_price=p_pos.effective_stop_price,
                        is_ratcheted=p_pos.is_ratcheted,
                        is_trailing=p_pos.is_trailing,
                        side=p_pos.side
                    )
                if persisted:
                    logger.info(f"[RISK GUARD] Restored {len(persisted)} active positions from SQLite state store.")
            except Exception as e:
                logger.debug(f"Note: Error restoring state in RiskGuard: {e}")

    def _persist_position(self, pos: PositionTracker, atr_15m: float = 0.0):
        if self.state_store:
            try:
                from bot.data.state_store import PersistedPosition
                self.state_store.save_position(PersistedPosition(
                    pair=pos.pair if pos.side == "LONG" else f"SHORT_{pos.pair}",
                    side=pos.side,
                    entry_price=pos.entry_price,
                    peak_price=pos.peak_price,
                    quantity=pos.quantity,
                    effective_stop_price=pos.effective_stop_price,
                    is_ratcheted=pos.is_ratcheted,
                    is_trailing=pos.is_trailing,
                    atr_15m=atr_15m,
                    updated_at=time.time()
                ))
            except Exception as e:
                logger.debug(f"Note: Error persisting position {pos.pair}: {e}")

    def update_positions_from_wallet(self, tickers: Dict[str, Any], exchange_info: Dict[str, Any], snapshot: Optional[MarketSnapshot] = None):
        """
        Synchronizes active position tracking with Roostoo wallet and short positions.
        Uses adaptive ATR stops and preserves historical entry prices across restarts.
        """
        balance_resp = self.roostoo.get_balance()
        wallet = (
            balance_resp.get("SpotWallet") or 
            balance_resp.get("Wallet") or 
            balance_resp.get("Data", {}).get("SpotWallet") or 
            balance_resp.get("Data", {}).get("Wallet") or {}
        )

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
                    # Adaptive ATR Stop-Loss: max(3.0%, 2.2 * ATR_15m) capped at MAX_ATR_STOP_LOSS_PCT (4.5%)
                    sym = f"{coin}USDT"
                    metric = snapshot.assets.get(sym) if snapshot else None
                    atr_pct = metric.atr_15m_pct if (metric and metric.atr_15m_pct > 0) else 1.0
                    dynamic_stop_pct = min(
                        settings.MAX_ATR_STOP_LOSS_PCT,
                        max(settings.HARD_STOP_LOSS_PCT, settings.DYNAMIC_ATR_MULTIPLIER * atr_pct)
                    )
                    stop_p = price * (1.0 - dynamic_stop_pct / 100.0)
                    new_pos = PositionTracker(
                        pair=pair,
                        entry_price=price,
                        peak_price=price,
                        quantity=tot_qty,
                        effective_stop_price=stop_p,
                        is_ratcheted=False,
                        is_trailing=False,
                        side="LONG"
                    )
                    self.active_positions[pair] = new_pos
                    self._persist_position(new_pos, atr_15m=atr_pct)
                    logger.info(
                        f"[RISK GUARD] Tracking new LONG position: {pair} @ ${price:,.4f} | "
                        f"Dynamic Stop ({dynamic_stop_pct:.2f}%): ${stop_p:,.4f}"
                    )
                else:
                    # Update quantity, calculate blended VWAP if size was added, and update peak
                    pos = self.active_positions[pair]
                    if tot_qty > pos.quantity and pos.quantity > 0:
                        added_qty = tot_qty - pos.quantity
                        pos.entry_price = (pos.entry_price * pos.quantity + price * added_qty) / tot_qty
                        # If trade hasn't moved into trailing/ratchet, update stop to anchor from new VWAP
                        if not pos.is_ratcheted and not pos.is_trailing:
                            sym = f"{coin}USDT"
                            metric = snapshot.assets.get(sym) if snapshot else None
                            atr_pct = metric.atr_15m_pct if (metric and metric.atr_15m_pct > 0) else 1.0
                            dynamic_stop_pct = min(
                                settings.MAX_ATR_STOP_LOSS_PCT,
                                max(settings.HARD_STOP_LOSS_PCT, settings.DYNAMIC_ATR_MULTIPLIER * atr_pct)
                            )
                            pos.effective_stop_price = pos.entry_price * (1.0 - dynamic_stop_pct / 100.0)
                        logger.info(f"[RISK GUARD] Position scaled up for {pair}: new VWAP Entry = ${pos.entry_price:,.4f}")
                    pos.quantity = tot_qty
                    if price > pos.peak_price and pos.side == "LONG":
                        pos.peak_price = price
                    self._persist_position(pos)

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
                    sentry = 0.0
                    for ep_field in ("EntryPrice", "entry_price", "OpenPrice", "open_price", "AvgPrice"):
                        if ep_field in spos and spos[ep_field]:
                            try:
                                sentry = float(spos[ep_field])
                                if sentry > 0:
                                    break
                            except (ValueError, TypeError):
                                pass
                    if sentry <= 0 and key in self.active_positions:
                        sentry = self.active_positions[key].entry_price
                    elif sentry <= 0:
                        sentry = tickers.get(spair, {}).get("LastPrice", 0.0)

                    sqty = float(spos.get("Quantity", spos.get("quantity", 0.0)))
                    sprice = tickers.get(spair, {}).get("LastPrice", sentry)

                    if key not in self.active_positions and sentry > 0:
                        stop_p = sentry * (1.0 + settings.SHORT_STOP_LOSS_PCT / 100.0)
                        new_pos = PositionTracker(
                            pair=spair,
                            entry_price=sentry,
                            peak_price=sprice,
                            quantity=sqty,
                            effective_stop_price=stop_p,
                            is_ratcheted=False,
                            is_trailing=False,
                            side="SHORT"
                        )
                        self.active_positions[key] = new_pos
                        self._persist_position(new_pos)
                        logger.info(f"[RISK GUARD] Tracking new SHORT position: {spair} @ ${sentry:,.4f} | Stop: ${stop_p:,.4f}")
                    elif key in self.active_positions:
                        pos = self.active_positions[key]
                        if sqty > pos.quantity and pos.quantity > 0:
                            added_qty = sqty - pos.quantity
                            pos.entry_price = (pos.entry_price * pos.quantity + sprice * added_qty) / sqty
                            if not pos.is_ratcheted and not pos.is_trailing:
                                pos.effective_stop_price = pos.entry_price * (1.0 + settings.SHORT_STOP_LOSS_PCT / 100.0)
                            logger.info(f"[RISK GUARD] Short position scaled up for {spair}: new VWAP Entry = ${pos.entry_price:,.4f}")
                        pos.quantity = sqty
                        if sprice < pos.peak_price:  # Track lowest price for short
                            pos.peak_price = sprice
                        self._persist_position(pos)
        except Exception as e:
            logger.debug(f"Note: Error checking short positions in risk guard: {e}")

        # Remove closed positions
        for p in list(self.active_positions.keys()):
            if p not in current_pairs:
                logger.info(f"[RISK GUARD] Position closed: {p}")
                del self.active_positions[p]
                if self.state_store:
                    self.state_store.remove_position(p)

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
                wallet_reset = bal_reset.get("SpotWallet") or bal_reset.get("Wallet") or {}
                reset_val = float(wallet_reset.get("USD", {}).get("Free", 0.0)) + float(wallet_reset.get("USD", {}).get("Lock", 0.0))
                for coin, val in wallet_reset.items():
                    if coin != "USD":
                        qty = float(val.get("Free", 0.0)) + float(val.get("Lock", 0.0))
                        p = tickers_reset.get(f"{coin}/USD", {}).get("LastPrice", 0.0)
                        reset_val += (qty * p)
                self.portfolio_high_watermark = reset_val
                if self.state_store:
                    self.state_store.save_circuit_breaker(self.portfolio_high_watermark, self.circuit_breaker_active, self.circuit_breaker_until)
                logger.info(f"[CIRCUIT BREAKER EXPIRED] Resuming operations. HWM reset to ${reset_val:,.2f}")

        ticker_resp = self.roostoo.get_ticker()
        tickers = ticker_resp.get("Data", {})

        # 1. Update High Watermark & Check Portfolio Circuit Breaker
        balance_resp = self.roostoo.get_balance()
        wallet = balance_resp.get("SpotWallet") or balance_resp.get("Wallet") or {}
        total_val = float(wallet.get("USD", {}).get("Free", 0.0)) + float(wallet.get("USD", {}).get("Lock", 0.0))

        for coin, val in wallet.items():
            if coin != "USD":
                qty = float(val.get("Free", 0.0)) + float(val.get("Lock", 0.0))
                tick_info = tickers.get(f"{coin}/USD", {})
                last_p = tick_info.get("LastPrice", 0.0)
                ask_p = tick_info.get("AskPrice", last_p)
                bid_p = tick_info.get("BidPrice", last_p)
                # Filter Spread Illusions: Use mid-price when bid/ask available to prevent false drawdown
                p = (ask_p + bid_p) / 2.0 if (ask_p > 0 and bid_p > 0) else last_p
                total_val += (qty * p)

        # UNIFIED EQUITY ACCOUNTING: Account for short collateral & unrealized PnL in risk guard total_val
        try:
            short_resp = self.roostoo.get_short_positions()
            active_shorts = short_resp.get("Positions", short_resp.get("Data", []))
            if isinstance(active_shorts, list):
                for spos in active_shorts:
                    spair = spos.get("Pair", spos.get("pair", ""))
                    scollat = float(spos.get("Collateral", spos.get("collateral", 0.0)))
                    sentry = float(spos.get("EntryPrice", spos.get("entry_price", spos.get("OpenPrice", 0.0))))
                    sqty = float(spos.get("Quantity", spos.get("quantity", 0.0)))
                    scurr_p = tickers.get(spair, {}).get("LastPrice", sentry)

                    if scollat <= 0 and sqty > 0 and sentry > 0:
                        scollat = sqty * sentry

                    upnl = (sentry - scurr_p) * sqty if (sentry > 0 and sqty > 0) else 0.0
                    short_equity = scollat + upnl
                    total_val += short_equity
        except Exception as e:
            logger.debug(f"Note: Error accounting for short equity in risk guard total_val: {e}")

        total_val = max(0.0, total_val)

        if self.portfolio_high_watermark <= 0.0 and total_val > 0.0:
            self.portfolio_high_watermark = total_val
            if self.state_store:
                self.state_store.save_circuit_breaker(self.portfolio_high_watermark, self.circuit_breaker_active, self.circuit_breaker_until)
        elif total_val > self.portfolio_high_watermark:
            self.portfolio_high_watermark = total_val
            if self.state_store:
                self.state_store.save_circuit_breaker(self.portfolio_high_watermark, self.circuit_breaker_active, self.circuit_breaker_until)

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
            if self.state_store:
                self.state_store.save_circuit_breaker(self.portfolio_high_watermark, self.circuit_breaker_active, self.circuit_breaker_until)
            return {"Status": "CIRCUIT_BREAKER_TRIGGERED", "Drawdown": drawdown_pct}

        # 2. Synchronize position state with adaptive ATR stops
        self.update_positions_from_wallet(tickers, snapshot.exchange_info, snapshot)

        # 3. Audit Individual Open Positions (Long and Short)
        for key, pos in list(self.active_positions.items()):
            try:
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
                    
                    # Intra-Period Peak Tracking: Stream candle high to catch violent wicks between 60s ticks
                    candle_high = metric.high_15m if (metric and metric.high_15m > 0) else curr_price
                    if candle_high > pos.peak_price:
                        pos.peak_price = candle_high
                    if curr_price > pos.peak_price:
                        pos.peak_price = curr_price

                    # Check Stage 1: Breakeven Profit Ratchet (+2.0% -> +0.4%)
                    if gain_pct >= settings.PROFIT_RATCHET_TRIGGER_PCT and not pos.is_ratcheted:
                        new_stop = pos.entry_price * (1.0 + settings.PROFIT_RATCHET_LOCK_PCT / 100.0)
                        if new_stop > pos.effective_stop_price:
                            pos.effective_stop_price = new_stop
                            pos.is_ratcheted = True
                            self._persist_position(pos)
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
                            self._persist_position(pos)
                            logger.info(
                                f"[TRAILING STOP LONG] {pos.pair} peak ${pos.peak_price:,.4f}. "
                                f"Trailing stop ratcheted to ${trail_stop:,.4f}"
                            )

                    # Check Order Flow Toxicity Emergency Exit with Price Confirmation (Anti Whip-Saw)
                    toxicity_thresh = getattr(settings, "TOXICITY_IMBALANCE_THRESHOLD", -0.25)
                    if metric and metric.taker_imbalance_15m < toxicity_thresh and metric.vol_zscore_15m > 2.5:
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

                # Check Stop-Loss Execution with Single-Tick Defense
                if curr_price <= pos.effective_stop_price:
                    tick_info = tickers.get(pos.pair, {})
                    bid_p = tick_info.get("BidPrice", curr_price)
                    # If Bid price is still above stop price, treat as transient single-tick wick anomaly
                    if bid_p > pos.effective_stop_price and curr_price <= pos.effective_stop_price:
                        logger.info(
                            f"[STOP DEFENSE] {pos.pair} last price ${curr_price:,.4f} wicked below stop ${pos.effective_stop_price:,.4f}, "
                            f"but Bid is ${bid_p:,.4f}. Holding."
                        )
                    else:
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

                    # Intra-Period Trough Tracking: Stream candle low to catch violent dips
                    candle_low = metric.low_15m if (metric and metric.low_15m > 0) else curr_price
                    if candle_low < pos.peak_price:
                        pos.peak_price = candle_low
                    if curr_price < pos.peak_price:
                        pos.peak_price = curr_price  # Track lowest price achieved

                    # Check Stage 1: Breakeven Profit Ratchet for Short (+2.0% drop -> lock +0.4%)
                    if gain_pct >= settings.PROFIT_RATCHET_TRIGGER_PCT and not pos.is_ratcheted:
                        new_stop = pos.entry_price * (1.0 - settings.PROFIT_RATCHET_LOCK_PCT / 100.0)
                        if new_stop < pos.effective_stop_price:
                            pos.effective_stop_price = new_stop
                            pos.is_ratcheted = True
                            self._persist_position(pos)
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
                            self._persist_position(pos)
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

                    # Check Short Stop-Loss Execution with Single-Tick Ask-Wall Defense
                    if curr_price >= pos.effective_stop_price:
                        tick_info = tickers.get(pos.pair, {})
                        ask_p = tick_info.get("AskPrice", curr_price)
                        # If Ask price is still below stop price, treat as transient single-tick wick anomaly
                        if ask_p < pos.effective_stop_price and curr_price >= pos.effective_stop_price:
                            logger.info(
                                f"[STOP DEFENSE SHORT] {pos.pair} last price ${curr_price:,.4f} wicked above stop ${pos.effective_stop_price:,.4f}, "
                                f"but Ask is ${ask_p:,.4f}. Holding short."
                            )
                        else:
                            loss_or_gain = "Short Stop-Loss" if curr_price > pos.entry_price else "Short Trailing Take-Profit"
                            logger.warning(
                                f"[{loss_or_gain.upper()} TRIGGERED] {pos.pair} Price: ${curr_price:,.4f} >= Stop: ${pos.effective_stop_price:,.4f}. "
                                f"Executing immediate short position close!"
                            )
                            self._execute_short_close(pos.pair, pos, curr_price, reason=loss_or_gain)
            except Exception as pos_err:
                logger.exception(f"[RISK GUARD ERROR] Error auditing position {pos.pair}: {pos_err}")

        return {"Status": "OK", "ActivePositions": len(self.active_positions)}

    def _execute_stop_sell(self, pair: str, pos: PositionTracker, price: float, exchange_info: Dict[str, Any], reason: str):
        amt_prec = exchange_info.get(pair, {}).get("AmountPrecision", 4)
        mini_order = exchange_info.get(pair, {}).get("MiniOrder", 1.0)
        
        # Verify strictly available free wallet balance to avoid Insufficient Balance error
        curr_free = pos.quantity
        try:
            b_resp = self.roostoo.get_balance()
            c_coin = pair.split("/")[0]
            b_wallet = b_resp.get("SpotWallet") or b_resp.get("Wallet") or {}
            curr_free = float(b_wallet.get(c_coin, {}).get("Free", pos.quantity))
            if curr_free < pos.quantity * 0.95:  # Balance locked by pending order
                self.roostoo.cancel_order(pair=pair)
                time.sleep(0.2)
                b_resp = self.roostoo.get_balance()
                b_wallet = b_resp.get("SpotWallet") or b_resp.get("Wallet") or {}
                curr_free = float(b_wallet.get(c_coin, {}).get("Free", curr_free))
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
        if self.state_store:
            self.state_store.remove_position(pair)
            if "Stop-Loss" in reason or "Toxicity" in reason or "Decay" in reason:
                self.state_store.quarantine_asset(pair, duration_hours=settings.QUARANTINE_DURATION_HOURS, reason=reason)

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
        if self.state_store:
            self.state_store.remove_position(key)
            if "Stop-Loss" in reason or "Squeeze" in reason:
                self.state_store.quarantine_asset(pair, duration_hours=settings.QUARANTINE_DURATION_HOURS, reason=reason)

    def _emergency_liquidate_all(self, tickers: Dict[str, Any], exchange_info: Dict[str, Any]):
        """
        Rate-limited emergency portfolio liquidation across all positions.
        Uses ThreadPoolExecutor bounded by MAX_CONCURRENT_LIQUIDATION_WORKERS to prevent HTTP 429 bans.
        """
        def _liquidate_item(item):
            key, pos = item
            curr_p = tickers.get(pos.pair, {}).get("LastPrice", pos.entry_price)
            time.sleep(0.1)  # Stagger requests to prevent 429 rate limit errors
            if pos.side == "SHORT":
                self._execute_short_close(pos.pair, pos, curr_p, reason="Circuit Breaker Short Liquidation")
            else:
                self._execute_stop_sell(pos.pair, pos, curr_p, exchange_info, reason="Circuit Breaker Full Liquidation")

        items = list(self.active_positions.items())
        if not items:
            return

        workers = min(settings.MAX_CONCURRENT_LIQUIDATION_WORKERS, len(items))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(_liquidate_item, items))
