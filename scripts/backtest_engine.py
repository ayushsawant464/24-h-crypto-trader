#!/usr/bin/env python3
"""
Multi-Timeframe Strategy Backtester & Hypothesis Validation Engine
Tests on:
- Period 1 (Oct 1 00:00 - Oct 5 00:00 UTC): Pre-competition baseline
- Period 2 (Oct 5 00:00 - Oct 6 12:00 UTC): Competition live window
Across:
- 1-Hour rebalance
- 4-Hour rebalance
- 15-Minute tactical execution
Includes realistic 0.1% taker fees per execution.
"""

import os
import json
import requests
import pandas as pd
import numpy as np

OCT_1_MS = 1790812800000  # 2026-10-01 00:00:00 UTC
OCT_5_MS = 1791158400000  # 2026-10-05 00:00:00 UTC

UNIVERSE = [
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ADAUSDT',
    'AVAXUSDT', 'NEARUSDT', 'DOGEUSDT', 'PEPEUSDT', 'TAOUSDT',
    'XRPUSDT', 'LINKUSDT', 'SUIUSDT'
]

FEE_TAKER = 0.001  # 0.1%
LOGS_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

def fetch_history(symbol, interval, start_ms, limit=600):
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "startTime": start_ms, "limit": limit}
    r = requests.get(url, params=params, timeout=10)
    if r.status_code != 200 or not r.json():
        return pd.DataFrame()
    raw = r.json()
    df = pd.DataFrame(raw, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades', 'tb_base_volume',
        'tb_quote_volume', 'ignore'
    ])
    for col in ['open', 'high', 'low', 'close', 'volume', 'quote_volume', 'tb_quote_volume']:
        df[col] = df[col].astype(float)
    df['trades'] = df['trades'].astype(int)
    df['open_time'] = df['open_time'].astype(int)
    return df

def run_backtest_strategy_a(df_dict, interval_step=4, lookback=3, btc_filter=True, stop_loss_pct=1.5):
    """
    Strategy A: Cross-Sectional Momentum with BTC Regime Filter & Real-time Stop-Loss
    interval_step: e.g. 4 for 4-hour rebalance if input is 1h klines.
    lookback: number of rebalance steps to compute momentum.
    """
    btc_df = df_dict['BTCUSDT'].copy().set_index('open_time')
    btc_df['ema20'] = btc_df['close'].ewm(span=20).mean()

    # Align all prices
    aligned = pd.DataFrame()
    for s in UNIVERSE:
        if s in df_dict:
            aligned[s] = df_dict[s].set_index('open_time')['close']
    aligned = aligned.dropna()

    timestamps = aligned.index
    portfolio_val = 100000.0
    equity_curve = [portfolio_val]
    current_weights = {s: 0.0 for s in UNIVERSE}
    current_entry_prices = {}
    trades_count = 0
    pnl_history = []

    # Step through time
    for i in range(lookback * interval_step, len(timestamps)):
        t = timestamps[i]
        curr_prices = aligned.loc[t]

        # 1. Micro risk-check on existing positions: Stop-loss trigger
        for s, w in list(current_weights.items()):
            if w > 0 and s in current_entry_prices:
                entry_p = current_entry_prices[s]
                curr_p = curr_prices[s]
                loss_pct = (curr_p - entry_p) / entry_p * 100
                if loss_pct <= -stop_loss_pct:
                    # Trigger stop-loss: liquidate to cash
                    pos_val = portfolio_val * w
                    fee = pos_val * FEE_TAKER
                    portfolio_val -= fee
                    current_weights[s] = 0.0
                    trades_count += 1

        # 2. Rebalancing checkpoint (every interval_step)
        if i % interval_step == 0:
            past_t = timestamps[i - lookback * interval_step]
            past_prices = aligned.loc[past_t]
            returns = (curr_prices - past_prices) / past_prices

            # Check BTC regime
            btc_bullish = True
            if btc_filter and t in btc_df.index:
                btc_p = btc_df.loc[t, 'close']
                btc_ema = btc_df.loc[t, 'ema20']
                btc_bullish = (btc_p >= btc_ema)

            # Determine target weights
            target_weights = {s: 0.0 for s in UNIVERSE}
            if btc_bullish:
                # Pick top 2 momentum assets
                top2 = returns.sort_values(ascending=False).index[:2]
                for s in top2:
                    if returns[s] > 0:  # Only long if positive absolute return
                        target_weights[s] = 0.40  # 40% each, 20% cash buffer
            else:
                # Bearish regime: Hold 80% Cash, or light short hedge
                target_weights['BTCUSDT'] = 0.0  # stay in cash

            # Execute rebalancing delta
            for s in UNIVERSE:
                delta_w = target_weights[s] - current_weights[s]
                if abs(delta_w) > 0.05:
                    trade_val = abs(delta_w) * portfolio_val
                    fee = trade_val * FEE_TAKER
                    portfolio_val -= fee
                    trades_count += 1
                    if target_weights[s] > 0 and current_weights[s] == 0:
                        current_entry_prices[s] = curr_prices[s]
                    current_weights[s] = target_weights[s]

        # Update portfolio value based on price changes
        if i < len(timestamps) - 1:
            next_t = timestamps[i + 1]
            next_prices = aligned.loc[next_t]
            period_gain = sum(
                current_weights[s] * (next_prices[s] - curr_prices[s]) / curr_prices[s]
                for s in UNIVERSE
            )
            portfolio_val *= (1.0 + period_gain)
            equity_curve.append(portfolio_val)
            pnl_history.append(period_gain)

    # Compute risk metrics
    eq = np.array(equity_curve)
    total_ret = (eq[-1] - eq[0]) / eq[0] * 100
    cummax = np.maximum.accumulate(eq)
    drawdowns = (eq - cummax) / cummax * 100
    max_dd = abs(min(drawdowns)) if len(drawdowns) > 0 else 0.01

    returns_arr = np.array(pnl_history)
    downside_returns = returns_arr[returns_arr < 0]
    downside_std = np.std(downside_returns) if len(downside_returns) > 0 else 1e-6
    total_std = np.std(returns_arr) if len(returns_arr) > 0 else 1e-6

    # Annualization factor for hourly: sqrt(24 * 365) = ~93.6
    sharpe = (np.mean(returns_arr) / total_std * 93.6) if total_std > 0 else 0
    sortino = (np.mean(returns_arr) / downside_std * 93.6) if downside_std > 0 else 0
    calmar = (total_ret / max_dd) if max_dd > 0 else 0
    composite = 0.4 * sortino + 0.3 * sharpe + 0.3 * calmar

    return {
        'total_ret_%': round(total_ret, 2),
        'max_dd_%': round(max_dd, 2),
        'trades': trades_count,
        'sharpe': round(sharpe, 2),
        'sortino': round(sortino, 2),
        'calmar': round(calmar, 2),
        'composite_score': round(composite, 2),
        'final_portfolio_$': round(portfolio_val, 2)
    }

def run_all_backtests():
    print("Loading 1h data...")
    df_1h = {}
    for s in UNIVERSE:
        df = fetch_history(s, '1h', OCT_1_MS, limit=200)
        if not df.empty:
            df_1h[s] = df

    # Split into Pre-Comp and Comp-Live
    pre_1h = {s: df[df['open_time'] < OCT_5_MS] for s, df in df_1h.items()}
    live_1h = {s: df[df['open_time'] >= OCT_5_MS] for s, df in df_1h.items()}

    print("\n--- Running Backtest Matrix on 1-Hour Data ---")
    results = []

    # 1. Pre-Competition (Oct 1 - Oct 4) with 4-Hour Rebalance
    res_pre_4h = run_backtest_strategy_a(pre_1h, interval_step=4, lookback=3, btc_filter=True, stop_loss_pct=1.5)
    res_pre_4h['period'] = 'Pre-Comp (Oct 1-4)'
    res_pre_4h['config'] = '4-Hour Rebalance + BTC Filter'
    results.append(res_pre_4h)

    # 2. Pre-Competition (Oct 1 - Oct 4) with 1-Hour Rebalance
    res_pre_1h = run_backtest_strategy_a(pre_1h, interval_step=1, lookback=12, btc_filter=True, stop_loss_pct=1.5)
    res_pre_1h['period'] = 'Pre-Comp (Oct 1-4)'
    res_pre_1h['config'] = '1-Hour Rebalance + BTC Filter'
    results.append(res_pre_1h)

    # 3. Competition Live (Oct 5 - Oct 6) with 4-Hour Rebalance
    res_live_4h = run_backtest_strategy_a(live_1h, interval_step=4, lookback=2, btc_filter=True, stop_loss_pct=1.5)
    res_live_4h['period'] = 'Comp-Live (Oct 5-6)'
    res_live_4h['config'] = '4-Hour Rebalance + BTC Filter'
    results.append(res_live_4h)

    # 4. Competition Live (Oct 5 - Oct 6) with 1-Hour Rebalance
    res_live_1h = run_backtest_strategy_a(live_1h, interval_step=1, lookback=6, btc_filter=True, stop_loss_pct=1.5)
    res_live_1h['period'] = 'Comp-Live (Oct 5-6)'
    res_live_1h['config'] = '1-Hour Rebalance + BTC Filter'
    results.append(res_live_1h)

    # 5. Full Period (Oct 1 - Oct 6) with 4-Hour Rebalance
    res_full_4h = run_backtest_strategy_a(df_1h, interval_step=4, lookback=3, btc_filter=True, stop_loss_pct=1.5)
    res_full_4h['period'] = 'Full (Oct 1-6)'
    res_full_4h['config'] = '4-Hour Rebalance + BTC Filter'
    results.append(res_full_4h)

    # 6. Full Period without Stop-Loss (Unhedged / Benchmark comparison)
    res_full_unhedged = run_backtest_strategy_a(df_1h, interval_step=4, lookback=3, btc_filter=False, stop_loss_pct=99.0)
    res_full_unhedged['period'] = 'Full (Oct 1-6)'
    res_full_unhedged['config'] = 'Unhedged (No Stop Loss, No BTC Filter)'
    results.append(res_full_unhedged)

    res_df = pd.DataFrame(results)[[
        'period', 'config', 'total_ret_%', 'max_dd_%', 'trades', 'sharpe', 'sortino', 'calmar', 'composite_score'
    ]]
    print(res_df.to_string(index=False))

    # Save to logs
    with open(os.path.join(LOGS_DIR, "backtest_results.json"), 'w') as f:
        json.dump(results, f, indent=2)

if __name__ == '__main__':
    run_all_backtests()
