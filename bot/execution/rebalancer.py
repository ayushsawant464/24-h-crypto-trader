import time
from typing import Dict, Any, Tuple
from bot.config.settings import settings
from bot.data.roostoo_client import RoostooClient
from bot.strategy.base import StrategyDecision
from bot.logs.logger import logger, log_trade

class PortfolioRebalancer:
    """
    Executes target weight allocations by computing delta orders
    and enforcing Roostoo precision rules (PricePrecision, AmountPrecision, MiniOrder).
    """
    def __init__(self, roostoo_client: RoostooClient):
        self.roostoo = roostoo_client

    def _get_portfolio_value_and_holdings(self, exchange_info: Dict[str, Any], tickers: Dict[str, Any]) -> Tuple[float, Dict[str, float]]:
        """
        Calculates total portfolio USD value and current asset quantities from /v3/balance.
        """
        balance_resp = self.roostoo.get_balance()
        wallet = balance_resp.get("Wallet", {})
        
        holdings = {}
        total_usd_value = 0.0

        # USD Cash
        usd_info = wallet.get("USD", {})
        free_usd = float(usd_info.get("Free", 0.0))
        locked_usd = float(usd_info.get("Lock", 0.0))
        total_usd_value += (free_usd + locked_usd)

        # Non-USD Crypto assets
        for coin, val in wallet.items():
            if coin == "USD":
                continue
            free_qty = float(val.get("Free", 0.0))
            lock_qty = float(val.get("Lock", 0.0))
            tot_qty = free_qty + lock_qty
            
            pair = f"{coin}/USD"
            price = tickers.get(pair, {}).get("LastPrice", 0.0)
            
            if tot_qty > 0 and price > 0:
                holdings[pair] = tot_qty
                total_usd_value += (tot_qty * price)

        return total_usd_value, holdings

    def execute_rebalance(self, decision: StrategyDecision, exchange_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Dispatches rebalancing orders to match target weights.
        """
        ticker_resp = self.roostoo.get_ticker()
        tickers = ticker_resp.get("Data", {})
        
        total_val, current_holdings = self._get_portfolio_value_and_holdings(exchange_info, tickers)
        logger.info(f"[REBALANCE CYCLE] Total Portfolio Value: ${total_val:,.2f} | Regime: {decision.regime}")

        if total_val <= 0:
            logger.error("Invalid portfolio value detected. Aborting rebalance.")
            return {"Success": False, "Error": "Invalid portfolio value"}

        # 1. First Pass: Sells / Liquidations (Free up USD cash)
        for pair, curr_qty in list(current_holdings.items()):
            target_w = decision.target_weights.get(pair, 0.0)
            curr_price = tickers.get(pair, {}).get("LastPrice", 0.0)
            if curr_price <= 0:
                continue

            curr_usd = curr_qty * curr_price
            target_usd = total_val * target_w
            delta_usd = target_usd - curr_usd

            # If we need to sell (delta_usd < 0)
            if delta_usd < -25.0:  # Avoid micro dust trades < $25
                sell_usd = abs(delta_usd)
                sell_qty = sell_usd / curr_price
                
                # Apply AmountPrecision
                prec_info = exchange_info.get(pair, {})
                amt_prec = prec_info.get("AmountPrecision", 4)
                mini_order = prec_info.get("MiniOrder", 1.0)
                
                sell_qty_rounded = round(sell_qty, amt_prec)
                if sell_qty_rounded * curr_price >= mini_order and sell_qty_rounded <= curr_qty:
                    logger.info(f"Rebalance SELL: {sell_qty_rounded} {pair} (~${sell_usd:.2f})")
                    resp = self.roostoo.place_order(pair=pair, side="SELL", quantity=sell_qty_rounded, order_type="MARKET")
                    order_id = resp.get("OrderId", resp.get("Data", {}).get("OrderId", "mock_id"))
                    log_trade(
                        symbol=pair,
                        side="SELL",
                        price=curr_price,
                        quantity=sell_qty_rounded,
                        order_id=order_id,
                        api_response=resp,
                        signal_reason=f"Rebalance rotation | Target: {target_w*100:.1f}%",
                        strategy_state={"regime": decision.regime}
                    )
                    time.sleep(0.3)

        # 2. Second Pass: Buys (Deploy capital to top momentum targets)
        # Refresh balance after sells
        time.sleep(1.0)
        total_val, current_holdings = self._get_portfolio_value_and_holdings(exchange_info, tickers)

        for pair, target_w in decision.target_weights.items():
            if pair == "USD":
                continue  # Cash buffer stays uninvested

            curr_price = tickers.get(pair, {}).get("LastPrice", 0.0)
            if curr_price <= 0:
                continue

            curr_qty = current_holdings.get(pair, 0.0)
            curr_usd = curr_qty * curr_price
            target_usd = total_val * target_w
            delta_usd = target_usd - curr_usd

            # If we need to buy (delta_usd > 0)
            if delta_usd > 25.0:
                buy_qty = delta_usd / curr_price
                prec_info = exchange_info.get(pair, {})
                amt_prec = prec_info.get("AmountPrecision", 4)
                mini_order = prec_info.get("MiniOrder", 1.0)

                buy_qty_rounded = round(buy_qty, amt_prec)
                if buy_qty_rounded * curr_price >= mini_order:
                    logger.info(f"Rebalance BUY: {buy_qty_rounded} {pair} (~${delta_usd:.2f})")
                    resp = self.roostoo.place_order(pair=pair, side="BUY", quantity=buy_qty_rounded, order_type="MARKET")
                    order_id = resp.get("OrderId", resp.get("Data", {}).get("OrderId", "mock_id"))
                    log_trade(
                        symbol=pair,
                        side="BUY",
                        price=curr_price,
                        quantity=buy_qty_rounded,
                        order_id=order_id,
                        api_response=resp,
                        signal_reason=decision.rationales.get(pair, "Momentum Target"),
                        strategy_state={"regime": decision.regime, "target_weight": target_w}
                    )
                    time.sleep(0.3)

        return {"Success": True, "Message": "Rebalance execution complete"}
