#!/usr/bin/env python3
"""
Deep Market Structure Analysis & Multi-Timeframe Hypothesis Backtester
Period: Oct 1, 2026 00:00 UTC - Oct 6, 2026 13:00 UTC
Includes:
- Oct 1-4 (Pre-Competition Baseline) vs Oct 5-6 (Competition Live Window)
- Order Flow & Participant Microstructure (Average Trade Size, Taker Flow, Alpha Classification)
- Hypothesis Testing & Multi-Timeframe Backtesting (15m, 1h, 4h) with 0.1% Taker Fees
"""

import os
import json
import datetime
import requests
import pandas as pd
import numpy as np

# Output trace file
LOGS_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
TRACE_FILE = os.path.join(LOGS_DIR, "deep_analysis_trace.json")

# Timestamps
OCT_1_MS = 1790812800000  # 2026-10-01 00:00:00 UTC
OCT_5_MS = 1791158400000  # 2026-10-05 00:00:00 UTC (Competition Start)

UNIVERSE = [
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ADAUSDT',
    'AVAXUSDT', 'NEARUSDT', 'DOGEUSDT', 'PEPEUSDT', 'TAOUSDT',
    'XRPUSDT', 'LINKUSDT', 'SUIUSDT'
]

def fetch_klines(symbol, interval, start_ms, limit=500):
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "startTime": start_ms, "limit": limit}
    r = requests.get(url, params=params, timeout=10)
    if r.status_code != 200:
        return pd.DataFrame()
    raw = r.json()
    if not raw:
        return pd.DataFrame()
    df = pd.DataFrame(raw, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades', 'tb_base_volume',
        'tb_quote_volume', 'ignore'
    ])
    for col in ['open', 'high', 'low', 'close', 'volume', 'quote_volume', 'tb_quote_volume']:
        df[col] = df[col].astype(float)
    df['trades'] = df['trades'].astype(int)
    df['datetime'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
    df['avg_trade_size'] = df['quote_volume'] / np.maximum(df['trades'], 1)
    df['taker_buy_ratio'] = df['tb_quote_volume'] / np.maximum(df['quote_volume'], 1e-9)
    return df

def analyze_period_breakdown():
    print("--- Fetching Hourly Data for All Symbols ---")
    data_1h = {}
    for s in UNIVERSE:
        df = fetch_klines(s, '1h', OCT_1_MS, limit=200)
        if not df.empty:
            data_1h[s] = df

    breakdown = []
    for s, df in data_1h.items():
        # Pre-competition: Oct 1 - Oct 4
        pre_df = df[df['open_time'] < OCT_5_MS]
        # Competition live: Oct 5 - Oct 6
        live_df = df[df['open_time'] >= OCT_5_MS]

        def calc_stats(sub_df):
            if sub_df.empty:
                return {}
            start_p = sub_df['open'].iloc[0]
            end_p = sub_df['close'].iloc[-1]
            ret = (end_p - start_p) / start_p * 100
            cummax = sub_df['close'].cummax()
            dd = ((sub_df['close'] - cummax) / cummax * 100).min()
            vol = sub_df['quote_volume'].sum() / 1e6
            avg_trade_val = sub_df['quote_volume'].sum() / max(sub_df['trades'].sum(), 1)
            tb_ratio = sub_df['tb_quote_volume'].sum() / max(sub_df['quote_volume'].sum(), 1e-9)
            hourly_vol = sub_df['close'].pct_change().std() * 100
            return {
                'return_%': round(ret, 2),
                'max_dd_%': round(dd, 2),
                'vol_m$': round(vol, 1),
                'avg_trade_usd': round(avg_trade_val, 1),
                'taker_buy_%': round(tb_ratio * 100, 2),
                'volatility_%': round(hourly_vol, 2)
            }

        pre_stats = calc_stats(pre_df)
        live_stats = calc_stats(live_df)

        breakdown.append({
            'symbol': s,
            'pre_comp_ret_%': pre_stats.get('return_%'),
            'pre_comp_dd_%': pre_stats.get('max_dd_%'),
            'pre_avg_trade_$': pre_stats.get('avg_trade_usd'),
            'live_comp_ret_%': live_stats.get('return_%'),
            'live_comp_dd_%': live_stats.get('max_dd_%'),
            'live_avg_trade_$': live_stats.get('live_avg_trade_usd', live_stats.get('avg_trade_usd')),
            'live_taker_buy_%': live_stats.get('taker_buy_%'),
            'live_vol_m$': live_stats.get('vol_m$')
        })

    breakdown_df = pd.DataFrame(breakdown).sort_values('live_comp_ret_%', ascending=False)
    return data_1h, breakdown_df

if __name__ == '__main__':
    data_1h, breakdown_df = analyze_period_breakdown()
    print("\n=== Pre-Competition (Oct 1-4) vs Competition (Oct 5-6) Breakdown ===")
    print(breakdown_df.to_string(index=False))
