#!/usr/bin/env python3
"""
Market Analysis Pipeline: Oct 2 - Oct 6, 2026
Analyzes price trends, order flow (taker buy ratios), beta coefficients,
liquidity capacity, and participant behavior across the Roostoo universe.
"""

import os
import json
import datetime
import requests
import pandas as pd
import numpy as np

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(OUTPUT_DIR, exist_ok=True)
TRACE_FILE = os.path.join(OUTPUT_DIR, "market_analysis_trace_oct2_oct6.json")

# Start: Oct 2 00:00 UTC (1790899200000 ms)
START_MS = 1790899200000

CRYPTO_SYMBOLS = [
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ADAUSDT',
    'AVAXUSDT', 'NEARUSDT', 'DOGEUSDT', 'PEPEUSDT', 'TAOUSDT',
    'XRPUSDT', 'LINKUSDT', 'SUIUSDT'
]

def fetch_crypto_history():
    data_by_symbol = {}
    for sym in CRYPTO_SYMBOLS:
        url = "https://api.binance.com/api/v3/klines"
        params = {"symbol": sym, "interval": "1h", "startTime": START_MS, "limit": 120}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            raw = r.json()
            df = pd.DataFrame(raw, columns=[
                'open_time', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_volume', 'trades', 'tb_base_volume',
                'tb_quote_volume', 'ignore'
            ])
            for col in ['open', 'high', 'low', 'close', 'volume', 'quote_volume', 'tb_quote_volume']:
                df[col] = df[col].astype(float)
            df['trades'] = df['trades'].astype(int)
            df['datetime'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
            data_by_symbol[sym] = df
    return data_by_symbol

def fetch_roostoo_snapshot():
    try:
        import time
        params = {"timestamp": str(int(time.time() * 1000))}
        r = requests.get("https://mock-api.roostoo.com/v3/ticker", params=params, timeout=10)
        if r.status_code == 200:
            return r.json().get("Data", {})
    except Exception as e:
        print(f"Error fetching Roostoo snapshot: {e}")
    return {}

def run_analysis():
    print("Running market analysis pipeline for Oct 2 - Oct 6...")
    crypto_data = fetch_crypto_history()
    roostoo_tickers = fetch_roostoo_snapshot()
    
    btc_df = crypto_data.get('BTCUSDT')
    if btc_df is None or btc_df.empty:
        raise RuntimeError("Failed to fetch BTC benchmark data")
    
    btc_ret = btc_df['close'].pct_change().dropna()
    
    symbol_metrics = []
    return_series = {}
    
    for sym, df in crypto_data.items():
        start_p = df['open'].iloc[0]
        end_p = df['close'].iloc[-1]
        tot_ret = (end_p - start_p) / start_p * 100
        
        # Max Drawdown
        cummax = df['close'].cummax()
        max_dd = ((df['close'] - cummax) / cummax * 100).min()
        
        # Volume & Taker Flow
        tot_quote_vol = df['quote_volume'].sum()
        tot_tb_quote_vol = df['tb_quote_volume'].sum()
        taker_buy_pct = (tot_tb_quote_vol / tot_quote_vol * 100) if tot_quote_vol > 0 else 50.0
        tot_trades = df['trades'].sum()
        
        # Returns series for beta & volatility
        rets = df['close'].pct_change().dropna()
        return_series[sym] = rets
        volatility_1h = rets.std() * 100
        
        # Beta against BTC
        cov = np.cov(rets, btc_ret)[0, 1] if len(rets) == len(btc_ret) else 0
        var_btc = np.var(btc_ret)
        beta = cov / var_btc if var_btc > 0 else 1.0
        
        # Roostoo live stats
        pair_name = sym.replace('USDT', '/USD')
        rt_info = roostoo_tickers.get(pair_name, {})
        rt_bid = rt_info.get('MaxBid', 0)
        rt_ask = rt_info.get('MinAsk', 0)
        rt_last = rt_info.get('LastPrice', end_p)
        rt_spread_pct = ((rt_ask - rt_bid) / rt_last * 100) if rt_last > 0 else 0
        rt_24h_vol = rt_info.get('UnitTradeValue', 0)
        
        symbol_metrics.append({
            'symbol': sym,
            'roostoo_pair': pair_name,
            'period_return_pct': round(tot_ret, 2),
            'max_drawdown_pct': round(max_dd, 2),
            'beta_to_btc': round(beta, 2),
            'hourly_volatility_pct': round(volatility_1h, 2),
            'taker_buy_pct': round(taker_buy_pct, 2),
            'binance_vol_usd_millions': round(tot_quote_vol / 1e6, 2),
            'roostoo_24h_vol_usd': round(rt_24h_vol, 2),
            'roostoo_spread_pct': round(rt_spread_pct, 4),
            'total_trades': int(tot_trades)
        })

    # Sort by return
    symbol_metrics.sort(key=lambda x: x['period_return_pct'], reverse=True)
    
    # Save trace output
    trace_data = {
        'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'period_start': '2026-10-02T00:00:00Z',
        'period_end': '2026-10-06T12:00:00Z',
        'metrics': symbol_metrics
    }
    
    with open(TRACE_FILE, 'w') as f:
        json.dump(trace_data, f, indent=2)
        
    print(f"Pipeline executed successfully. Trace saved to {TRACE_FILE}")
    return symbol_metrics

if __name__ == "__main__":
    metrics = run_analysis()
    df = pd.DataFrame(metrics)
    print("\n--- Summary of Oct 2 - Oct 6 Market Behavior ---")
    print(df[['symbol', 'period_return_pct', 'max_drawdown_pct', 'beta_to_btc', 'taker_buy_pct', 'roostoo_spread_pct']].to_string(index=False))
