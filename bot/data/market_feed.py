import time
import requests
import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from bot.config.settings import settings
from bot.data.roostoo_client import RoostooClient
from bot.logs.logger import logger

@dataclass
class AssetMetrics:
    symbol: str
    roostoo_pair: str
    last_price: float
    spread_pct: float
    volume_24h_usd: float
    return_12h_pct: float
    return_4h_pct: float
    return_15m_pct: float
    taker_buy_4h_pct: float
    taker_imbalance_15m: float
    vol_zscore_15m: float
    atr_15m_pct: float
    beta_to_btc: float
    residual_alpha_pct: float
    is_liquid: bool
    lag_spread_4h_pct: float = 0.0

@dataclass
class MarketSnapshot:
    timestamp: float
    btc_above_ema20: bool
    btc_taker_buy_pct: float
    btc_atr_normal: bool
    assets: Dict[str, AssetMetrics]
    exchange_info: Dict[str, Any]

class MarketFeed:
    """
    Unified market data aggregator pulling live ticker/orderbook from Roostoo
    and historical kline/order-flow telemetry from Binance.
    """
    def __init__(self, roostoo_client: RoostooClient):
        self.roostoo = roostoo_client
        self.binance_base_url = settings.BINANCE_BASE_URL
        self._exchange_info_cache = None
        self._cache_time = 0

    def get_exchange_info(self) -> Dict[str, Any]:
        now = time.time()
        if self._exchange_info_cache is None or (now - self._cache_time) > 3600:
            info = self.roostoo.get_exchange_info()
            if info.get("TradePairs"):
                self._exchange_info_cache = info.get("TradePairs", {})
                self._cache_time = now
        return self._exchange_info_cache or {}

    def fetch_binance_klines(self, symbol: str, interval: str = "1h", limit: int = 40) -> pd.DataFrame:
        url = f"{self.binance_base_url}/api/v3/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        try:
            r = requests.get(url, params=params, timeout=5)
            if r.status_code == 200 and r.json():
                df = pd.DataFrame(r.json(), columns=[
                    'open_time', 'open', 'high', 'low', 'close', 'volume',
                    'close_time', 'quote_volume', 'trades', 'tb_base', 'tb_quote', 'ignore'
                ])
                for col in ['open', 'high', 'low', 'close', 'volume', 'quote_volume', 'tb_quote']:
                    df[col] = df[col].astype(float)
                return df
        except Exception as e:
            logger.warning(f"Error fetching Binance klines for {symbol}: {e}")
        return pd.DataFrame()

    def capture_snapshot(self, tracked_symbols: List[str]) -> MarketSnapshot:
        """
        Gathers complete multi-timeframe order flow snapshot across universe.
        """
        ex_info = self.get_exchange_info()
        ticker_resp = self.roostoo.get_ticker()
        tickers = ticker_resp.get("Data", {})

        # Fetch BTC benchmark first (1h and 15m)
        btc_1h = self.fetch_binance_klines("BTCUSDT", interval="1h", limit=40)
        btc_15m = self.fetch_binance_klines("BTCUSDT", interval="15m", limit=30)
        
        btc_above_ema20 = True
        btc_tb_pct = 50.0
        btc_atr_normal = True
        btc_ret_12h = 0.0
        btc_ret_4h = 0.0

        if not btc_1h.empty:
            ema20 = btc_1h['close'].ewm(span=20).mean().iloc[-1]
            last_btc = btc_1h['close'].iloc[-1]
            btc_above_ema20 = bool(last_btc >= ema20)
            btc_tb_pct = (btc_1h['tb_quote'].iloc[-4:].sum() / max(btc_1h['quote_volume'].iloc[-4:].sum(), 1e-9)) * 100
            btc_ret_12h = (btc_1h['close'].iloc[-1] - btc_1h['close'].iloc[-12]) / btc_1h['close'].iloc[-12] * 100
            if len(btc_1h) >= 5:
                btc_ret_4h = (btc_1h['close'].iloc[-1] - btc_1h['close'].iloc[-5]) / btc_1h['close'].iloc[-5] * 100

        if not btc_15m.empty:
            tr = np.maximum(
                btc_15m['high'] - btc_15m['low'],
                np.maximum(
                    abs(btc_15m['high'] - btc_15m['close'].shift(1)),
                    abs(btc_15m['low'] - btc_15m['close'].shift(1))
                )
            )
            atr_recent = tr.iloc[-1]
            atr_avg = tr.rolling(20).mean().iloc[-1]
            btc_atr_normal = bool(atr_recent < 2.2 * max(atr_avg, 1e-6))

        asset_metrics = {}
        for sym in tracked_symbols:
            pair = sym.replace("USDT", "/USD")
            t_data = tickers.get(pair, {})
            bid = t_data.get("MaxBid", 0.0)
            ask = t_data.get("MinAsk", 0.0)
            last_p = t_data.get("LastPrice", 0.0)
            vol_24h = t_data.get("UnitTradeValue", 0.0)
            spread_pct = ((ask - bid) / last_p * 100) if last_p > 0 else 0.0

            df_1h = self.fetch_binance_klines(sym, interval="1h", limit=30)
            df_15m = self.fetch_binance_klines(sym, interval="15m", limit=30)

            if df_1h.empty or len(df_1h) < 13:
                continue

            # 12h & 4h returns
            ret_12h = (df_1h['close'].iloc[-1] - df_1h['close'].iloc[-13]) / df_1h['close'].iloc[-13] * 100
            ret_4h = (df_1h['close'].iloc[-1] - df_1h['close'].iloc[-5]) / df_1h['close'].iloc[-5] * 100
            tb_4h_pct = (df_1h['tb_quote'].iloc[-4:].sum() / max(df_1h['quote_volume'].iloc[-4:].sum(), 1e-9)) * 100

            # 15m order flow & toxicity
            ret_15m = 0.0
            tb_imbalance_15m = 0.0
            vol_z = 0.0
            atr_15m_pct = 1.0

            if not df_15m.empty and len(df_15m) >= 20:
                ret_15m = (df_15m['close'].iloc[-1] - df_15m['open'].iloc[-1]) / df_15m['open'].iloc[-1] * 100
                net_tb = df_15m['tb_quote'].iloc[-1] - (df_15m['quote_volume'].iloc[-1] - df_15m['tb_quote'].iloc[-1])
                tb_imbalance_15m = net_tb / max(df_15m['quote_volume'].iloc[-1], 1e-9)
                vol_mean = df_15m['quote_volume'].rolling(20).mean().iloc[-1]
                vol_std = df_15m['quote_volume'].rolling(20).std().iloc[-1]
                vol_z = (df_15m['quote_volume'].iloc[-1] - vol_mean) / max(vol_std, 1e-6)

                tr = np.maximum(
                    df_15m['high'] - df_15m['low'],
                    np.maximum(
                        abs(df_15m['high'] - df_15m['close'].shift(1)),
                        abs(df_15m['low'] - df_15m['close'].shift(1))
                    )
                )
                atr_15m_pct = (tr.rolling(14).mean().iloc[-1] / df_15m['close'].iloc[-1]) * 100

            # Beta & Residual Alpha vs BTC
            beta = 1.0
            if not btc_1h.empty and len(btc_1h) >= 24:
                alt_rets = df_1h['close'].pct_change().dropna().iloc[-24:]
                b_rets = btc_1h['close'].pct_change().dropna().iloc[-24:]
                if len(alt_rets) == len(b_rets):
                    cov = np.cov(alt_rets, b_rets)[0, 1]
                    var_b = np.var(b_rets)
                    beta = (cov / var_b) if var_b > 0 else 1.0

            # Residual Alpha: Alt Return - Beta * BTC Return
            residual_alpha = ret_12h - (beta * btc_ret_12h)

            # Cross-Crypto Lag Spread: Expected Beta Move - Actual Alt Move (4h horizon)
            # Positive value indicates altcoin is lagging BTC breakout and due for catch-up drift
            lag_spread_4h = (beta * btc_ret_4h) - ret_4h

            is_liquid = bool(vol_24h >= settings.MIN_24H_VOL_USD and spread_pct <= settings.MAX_SPREAD_PCT)

            asset_metrics[sym] = AssetMetrics(
                symbol=sym,
                roostoo_pair=pair,
                last_price=last_p if last_p > 0 else df_1h['close'].iloc[-1],
                spread_pct=spread_pct,
                volume_24h_usd=vol_24h,
                return_12h_pct=ret_12h,
                return_4h_pct=ret_4h,
                return_15m_pct=ret_15m,
                taker_buy_4h_pct=tb_4h_pct,
                taker_imbalance_15m=tb_imbalance_15m,
                vol_zscore_15m=vol_z,
                atr_15m_pct=atr_15m_pct,
                beta_to_btc=beta,
                residual_alpha_pct=residual_alpha,
                is_liquid=is_liquid,
                lag_spread_4h_pct=lag_spread_4h
            )

        return MarketSnapshot(
            timestamp=time.time(),
            btc_above_ema20=btc_above_ema20,
            btc_taker_buy_pct=btc_tb_pct,
            btc_atr_normal=btc_atr_normal,
            assets=asset_metrics,
            exchange_info=ex_info
        )
