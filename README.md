# Roostoo Autonomous Quantitative Trading Bot

An institutional-grade, autonomous quantitative trading system designed for the Roostoo Hackathon. The bot deploys a multi-timeframe decision engine capturing cross-sectional momentum confirmed by institutional taker order flow, safeguarded by a real-time risk daemon that clamps drawdowns to maximize the Composite Performance Score ($0.4 \times \text{Sortino} + 0.3 \times \text{Sharpe} + 0.3 \times \text{Calmar}$).

---

## 1. Project Overview

### Strategy Summary
The system executes a **calculating, multi-timeframe regime-switching strategy**:
* **Macro Layer (8-Hour Rebalancing)**: Scans the liquid crypto universe, confirms positive market regime via Bitcoin order flow, identifies outperforming assets with positive residual alpha ($\epsilon = R_{\text{alt}} - \beta R_{\text{BTC}}$), and verifies institutional conviction using rolling 4-hour Taker Buy ratios ($>51.0\%$).
* **Micro Layer (1-Minute Risk Guard)**: Continuously audits all active positions in real time to enforce volatility-adjusted stop losses, breakeven profit ratchets (+2.0%), trailing profit locks (+4.0%), order flow toxicity emergency exits, and a 2.0% portfolio circuit breaker.

### High-Level Idea
Crypto markets exhibit strong cross-sectional momentum drift driven by sector capital rotations, which institutional desks execute via aggressive taker orders across multi-hour TWAP windows. By capturing this informed flow on an 8-hour horizon, we eliminate high-frequency fee drag ($0.2\%$ round-trip) while riding winning trends. In parallel, our real-time micro daemon neutralizes sudden market dumps, keeping Max Drawdown $<2.5\%$ to maximize the Sortino ($40\%$) and Calmar ($30\%$) metrics.

### Key Features
* **Zero Manual Intervention**: 100% autonomous execution compliant with Screen 1 requirements.
* **Positive Expected Value Gate**: Trades are executed only when net expectancy $\mathbb{E}[R_{\text{net}}] \ge +0.80\%$ after accounting for commissions and spreads.
* **Order Flow Toxicity Protection**: Real-time volume imbalance sensors detect whale distribution and insider pre-dumps before price collapses.
* **Audit-Proof JSONL Trace**: Full trade logging (`logs/trade_execution_trace.jsonl`) recording order IDs, timestamps, sides, quantities, API responses, and rationale.

---

## 2. Architecture & System Design

```
                                  [DATA MODULE]
           Roostoo REST API (Tickers / Balances)  +  Binance Public Feed (Order Flow)
                                        │
                                        ▼
                            [STRATEGY DECISION ENGINE]
                     Evaluates the 5 Calculating Entry Gates:
              1. Macro Regime ──► 2. Liquidity ──► 3. Toxicity ──► 4. Alpha ──► 5. Sizing
                                        │
                                        ▼
                       [EXECUTION REBALANCER (8-Hour Loop)]
               Computes Target Deltas & Enforces Lot / Precision Rules
                      Dispatches Spot Market Orders via HMAC API
                                        │
                                        ▼
                        [RISK GUARD DAEMON (1-Minute Loop)]
               Monitors Stops, Profit Ratchets & 2.0% Portfolio Circuit Breaker
                                        │
                                        ▼
                             [STRUCTURED AUDIT LOGGER]
                   Persists Execution Records in JSONL for Code Review
```

### Modular Components
* **`bot/data/roostoo_client.py`**: Authenticated API client handling HMAC SHA256 signatures, millisecond timestamp drift, and exponential backoff retry.
* **`bot/data/market_feed.py`**: Aggregates live Roostoo prices/spreads with Binance 1h/15m klines and order flow metrics (Taker Buy %, ATR, Beta, Residual Alpha).
* **`bot/strategy/decision_engine.py`**: Implements the 5 mathematical entry gates, EV calculations, and volatility-adjusted sizing.
* **`bot/execution/rebalancer.py`**: Rebalances portfolio holdings to target weights, strictly respecting `AmountPrecision`, `PricePrecision`, and `MiniOrder`.
* **`bot/execution/risk_guard.py`**: Real-time daemon enforcing stops, profit locks, and the macro portfolio circuit breaker.
* **`bot/logs/logger.py`**: Structured logger satisfying Screen 1 verification standards.

### Tech Stack
* **Language**: Python 3.11+
* **Libraries**: `requests`, `pandas`, `numpy`, `python-dotenv`
* **Testing**: Python standard library `unittest`
* **Containerization**: Docker (Debian slim)

---

## 3. Strategy Explanation

### The 5 Calculating Entry Gates
An asset is allocated capital if and only if it passes all five gates:
1. **Gate 1 (Macro Regime)**: Bitcoin 1h close $\ge \text{EMA}_{20}$, BTC 4h Taker Buy $\ge 45\%$, and BTC $15\text{m ATR} < 2.2 \times \overline{\text{ATR}}_{20}$. If failed, 100% of capital is parked in the USD Cash Bunker.
2. **Gate 2 (Liquidity & Spread)**: 24h USD Volume $\ge \$5,000,000$, Bid-Ask Spread $\le 0.035\%$, and order capacity $\le 0.5\%$ of hourly volume.
3. **Gate 3 (Order Flow Toxicity)**: 4h Taker Buy Ratio $\ge 51.0\%$, 15m Taker Imbalance $\ge 0.0$, and zero recent high-volume red dumping candles.
4. **Gate 4 (Alpha Identification)**: 12h Return $\ge +1.5\%$ and Positive Residual Alpha ($\epsilon = R_{\text{alt}} - \beta R_{\text{BTC}} > 0$).
5. **Gate 5 (Positive Expectancy & Sizing)**: Net expected value $\mathbb{E}[R_{\text{net}}] \ge +0.80\%$. Position size weighted by Inverse ATR:
   $$W_i = \min\left(0.30, \frac{1.0\%}{\text{ATR}_{15\text{m}, \%}}\right)$$
   *Max single asset: 30%, Max total exposure: 80%, Guaranteed Cash Reserve: 20%.*

### Exit Conditions & Risk Rules
* **Hard Stop-Loss**: Market sell at $-\max(3.0\%, 2.2 \times \text{ATR}_{15\text{m}})$.
* **Breakeven Profit Ratchet**: When unrealized gain reaches $+2.0\%$, ratchet stop to $+0.40\%$ (guaranteeing transaction fees are covered).
* **Trailing Profit Lock**: When unrealized gain reaches $+4.0\%$, trail stop at $\text{Peak Price} - 1.2\%$.
* **Toxicity Emergency Exit**: Immediate exit if 15m Taker Imbalance drops $<-25\%$ with volume spike $>2.5\sigma$.
* **Portfolio Circuit Breaker**: If total portfolio value drops $>2.0\%$ from historical peak, liquidate 100% into USD Cash and freeze trading for 4 hours.

---

## 4. Setup Instructions & How to Run the Bot

### Prerequisites
* Python 3.11+
* Git
* Roostoo API credentials

### Local Installation
```bash
# 1. Clone repository
git clone <repo-url>
cd sushack

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure credentials
cp .env.example .env
# Edit .env and enter your ROOSTOO_API_KEY and ROOSTOO_SECRET_KEY
```

### Running Unit & Integration Tests
```bash
python -m unittest discover tests -v
```

### Running the Bot
```bash
python main.py
```

### Running via Docker
```bash
# Build Docker image
docker build -t roostoo-bot .

# Run container with environment variables
docker run -d --name roostoo-trading-bot --env-file .env roostoo-bot
```

### Monitoring Activity
* Live application logs: `tail -f logs/bot_activity.log`
* Audit trade trace: `cat logs/trade_execution_trace.jsonl`
