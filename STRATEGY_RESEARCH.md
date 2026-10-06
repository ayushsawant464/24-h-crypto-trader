# Quantitative Strategy Playbook & Market Research

This document serves as our persistent blueprint for strategy design, market microstructure analysis, risk hedging frameworks, and execution rules for the Roostoo Quantitative Trading Competition.

---

## 1. The Core Objective & Evaluation Mechanics

To win, our system must satisfy all four screening tiers in sequence:

```
[Screen 1: Rule Compliance] ──► [Screen 2: Net Returns] ──► [Screen 3: Composite Score] ──► [Screen 4: Technical Review]
  - 100% Autonomous             - Top 20 per region           - 0.4 * Sortino               - Clean, modular code
  - Clean git history           - Net Portfolio Return        - 0.3 * Sharpe                - Robust logging
  - No HFT / No Arbitrage                                     - 0.3 * Calmar                - 24/7 AWS runnability
```

### The Winning Metric Formula
$$\text{Composite Score} = 0.4 \times \text{Sortino} + 0.3 \times \text{Sharpe} + 0.3 \times \text{Calmar}$$

* **Sortino ($40\%$)**: Penalizes solely downside deviation ($\sigma_{\text{down}}$). Large gains do not lower this metric.
* **Calmar ($30\%$)**: $\frac{\text{Annualized Return}}{\text{Max Drawdown}}$. Max Drawdown is in the denominator; keeping drawdown low (e.g., $<3\%$) multiplies the Calmar score exponentially.
* **Sharpe ($30\%$)**: Penalizes total return variance.
* **Strategic Takeaway**: Risk management and drawdown defense account for **70% of the finalist evaluation**.

---

## 2. Product Universe & Asset Behavior on Roostoo

From live exchange inspection (`/v3/exchangeInfo`), Roostoo supports **88 assets**:
* **67 Crypto Pairs** (Mega-caps, Layer-1/2s, DeFi, Meme coins, AI tokens)
* **21 Tokenized Equities** (`AMDB`, `NVDAB`, `TSLAB`, `MSFTB`, `MSTRB`, `COINB`, `PLTRB`, etc.)

### Empirical Class Behaviors
1. **Mega-Caps (`BTC/USD`, `ETH/USD`, `SOL/USD`)**:
   - High liquidity, lowest bid-ask spreads.
   - Sets market trend/beta. All altcoins react to BTC price action with a minor lag.
2. **High-Beta Mid-Caps (`AVAX`, `SUI`, `NEAR`, `APT`, `LINK`, `ADA`)**:
   - High positive correlation to BTC ($\rho \approx 0.75 - 0.90$), with beta $\beta \approx 1.5 - 2.5$.
   - Outperform on upside breakout; crash aggressively on market pullbacks.
3. **Meme & Narrative Coins (`DOGE`, `PEPE`, `WIF`, `BONK`, `TAO`, `PUMP`)**:
   - Heavy momentum persistence followed by violent mean-reversion and long drawdowns.
   - Dangerous unhedged, but offer high alpha when traded with tight trailing stops.
4. **Tokenized Equities & Crypto Proxies (`MSTRB`, `COINB`)**:
   - `MSTRB` (MicroStrategy) and `COINB` (Coinbase) act as equity-wrapped high-beta Bitcoin plays.
   - Trade primarily during US equity market sessions; can exhibit pricing divergence over weekends.
5. **Safe-Haven Commodity (`PAXG/USD`) & Cash (`USD`)**:
   - `PAXG` tracks gold; uncorrelated with crypto sell-offs.
   - Cash USD generates **zero downside variance and zero drawdown**, serving as an active risk-off allocation tool.

---

## 3. Recommended Strategies for the Competition

### Strategy Option A: Cross-Sectional Relative Momentum + Beta Hedging (Primary Recommendation)
* **Core Idea**: Capital rotates rapidly across crypto assets. We dynamically long the strongest outperforming assets while shielding downside risk via a trend-filtered short or cash hedge.
* **Rules**:
  1. Every candle close (e.g., 1-Hour), rank the top 15 liquid assets by risk-adjusted momentum (e.g., ROC / Volatility or EMA Slope).
  2. Enter **Long** positions on the top 2 ranked assets.
  3. **Regime Filter**:
     - If `BTC/USD` is above its 20-period EMA: Market is Risk-On (run unhedged or light hedge).
     - If `BTC/USD` drops below its 20-period EMA: Market is Risk-Off (hedge by shorting `BTC/USD` via `/v6/short_open` or moving 70% of portfolio to Cash).
* **Why it wins**: Generates high returns during market pumps, while avoiding catastrophic drawdowns during crashes.

### Strategy Option B: Cointegrated Relative-Value Pairs (Statistical Long/Short)
* **Core Idea**: Trade pairs of assets that historically move together. When their spread diverges beyond statistical norms, take a market-neutral position anticipating mean reversion.
* **Candidate Pairs**:
  - `ETH/USD` vs `BTC/USD` (Standard crypto macro spread)
  - `SOL/USD` vs `ETH/USD` (L1 competitor spread)
  - `DOGE/USD` vs `SHIB/USD` (Meme coin spread)
  - `MSTRB/USD` vs `BTC/USD` (Equity vs Crypto relative value)
* **Rules**:
  1. Track the log-price spread: $S_t = \ln(P_A) - \beta \ln(P_B)$.
  2. Calculate rolling Z-score over a 48-period window: $Z_t = \frac{S_t - \mu}{\sigma}$.
  3. When $Z_t > +2.0$: Short Asset A (via `/v6/short_open`) and Long Asset B.
  4. When $Z_t < -2.0$: Long Asset A and Short Asset B (via `/v6/short_open`).
  5. Close both legs when $Z_t$ returns to $0.0$.
* **Why it wins**: Zero directional market exposure. Even if Bitcoin dumps 20%, the long/short spread is protected, giving high Sharpe and Calmar scores.

---

## 4. Time Horizon Determination

| Time Horizon | Characteristics | Suitability for Hackathon | Verdict |
| :--- | :--- | :--- | :--- |
| **High Frequency / Scalping (< 1 min)** | Millisecond execution, thousands of orders. | **Violates Rules** (Screen 1 bans HFT/Arbitrage; triggers API rate limits; 0.1% taker fees destroy profit). | **STRICTLY REJECTED** |
| **Short Intraday (5m - 15m)** | Frequent trades, high market noise. | High fee drag ($0.2\%$ round-trip eats small $0.3\%$ gains); potential rate-limit friction. | **Sub-optimal** |
| **Swing / Tactical (1h - 4h)** | Strong trend signals, 2%–6% profit targets. | **Optimal**: Polling every 1–5 min is completely within API limits; fee friction is $<5\%$ of trade gain; generates 15–40 high-conviction trades across hackathon. | **RECOMMENDED (Sweet Spot)** |
| **Position / Macro (1d - 1w)** | Multi-week holding period. | Too slow for a 3–14 day hackathon; produces only 1–2 trades (statistically insufficient for Screen 3). | **Too Slow** |

---

## 5. Evaluation of Qualitative News & Sentiment

### What is Qualitative News in Crypto?
* Regulatory headlines (SEC approvals, lawsuits, government policies).
* Macroeconomic releases (US CPI inflation, FOMC interest rate announcements).
* Crypto-specific events (ETF inflow/outflow reports, exchange listings, token unlocks).
* Narrative shifts (e.g., AI agent tokens, meme coin runs, Layer-2 launches).
* Exploit & hack reports (smart contract vulnerabilities, bridge compromises).

### Can News Help Understand Market Scenarios?
* **Yes, for Macro Regime Identification**: News helps explain *why* the market is risk-on or risk-off. Understanding if liquidity is expanding or contracting provides macro context.

### Can We Trust News for Alpha Generation? (The Quantitative Reality)
1. **Speed & Latency Trap**: In crypto, institutional HFT firms read news feeds in milliseconds via direct exchange APIs. By the time news reaches a retail RSS feed, Twitter/X, or an LLM parser, the price move is already fully baked in.
2. **"Buy the Rumor, Sell the News"**: Positive news frequently marks the exact local top of a rally as smart money uses exit liquidity.
3. **Factual Noise & Manipulation**: Crypto news is replete with false rumors, bot spam, and sensationalism.
4. **Platform & Cost Constraint**: The competition rules explicitly state: *"Roostoo will only cover cloud server costs; any additional data source costs (such as LLM API calls) are not covered."* Relying on live external news APIs introduces external failure points, latency, and financial costs.

### How Quantitative Systems Safely Exploit Sentiment
Instead of parsing unstructured raw text, robust quantitative bots use **Quantitative Proxies of Sentiment**:
* **Volume Spikes**: Abnormal trading volume relative to 20-day average indicates institutional conviction.
* **Relative Strength Index (RSI) & Breakouts**: Identifies where capital is actively flowing in real time.
* **Volatility Regime (ATR Expansion)**: Warns of incoming market turmoil so the bot can reduce position sizing or activate trailing stops.

---

## 6. Modular 4-Hour Rebalancing & Execution Pipeline

To keep strategies flexible, pluggable, and verifiable, the execution pipeline is strictly decoupled from the mathematical models:

```
[4-Hour Timer / Cron]
         │
         ▼
[BaseStrategy Interface: generate_target_weights(market_snapshot)]
         │
         ├── Outputs target portfolio distribution:
         │   e.g. {'SOL/USD': +0.25, 'SUI/USD': +0.25, 'BTC/USD': -0.20, 'USD': +0.30}
         │
         ▼
[Portfolio Rebalancer]
         ├── Fetches current wallet balance via /v3/balance
         ├── Queries exchange precision & mini-order limits via /v3/exchangeInfo
         ├── Computes net delta order for each asset
         └── Executes spot orders via /v3/place_order or shorts via /v6/short_open / /v6/short_close
         │
         ▼
[Real-Time 1-Minute Risk Guard Daemon]
         ├── Monitors active positions against entry prices every 60 seconds
         ├── Triggers Hard Stop-Loss (-1.2% threshold)
         ├── Engages Trailing Profit Lock (+2.5% threshold -> trailing 0.8% offset)
         └── Enforces Portfolio Circuit Breaker (-2.0% drawdown threshold)
         │
         ▼
[Audit & Traceability Logger]
         └── Every decision, weight, API request/response, and PnL tick is recorded in
             persistent structured logs (logs/execution_trace.jsonl) for Screen 1 validation.
```

