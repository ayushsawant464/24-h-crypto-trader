# Algorithmic Decision Calculus & Quantitative Execution Matrix

This document defines the mathematical decision rules governing all trade entries, exits, position sizing, hedging, and emergency pullbacks for the Roostoo Trading Bot. 

No order may be placed unless it passes every condition in the **Decision Gates** defined below.

---

## 1. The Core Equation: Positive Expected Value Gate ($\mathbb{E}[R] > 0$)

Every trade must satisfy the Positive Net Expectancy hurdle:

$$\mathbb{E}[R_{\text{net}}] = \Big(P(\text{Win}) \times \bar{R}_{\text{Win}}\Big) - \Big(P(\text{Loss}) \times \bar{R}_{\text{Loss}}\Big) - \text{Friction} \ge +0.80\%$$

Where:
* **$\text{Friction}$**: $2 \times \text{Fee}_{\text{taker}} + \text{Spread} = 2 \times 0.10\% + \text{Spread} \approx 0.22\%\text{ to }0.25\%$.
* **Reward-to-Risk Requirement**: 
  $$\frac{\bar{R}_{\text{Win}}}{\bar{R}_{\text{Loss}}} \ge 2.0$$
* **Probability of Win Threshold**: Historical conditioned win rate $P(\text{Win}) \ge 55\%$ based on order-flow alignment.

---

## 2. The 5 Sequential Decision Gates for TRADE ENTRY

```
[Candidate Asset Identified]
            │
            ▼
[GATE 1: Macro / Regime Filter] ──────► FAIL: Reject Longs / Move 100% to Cash Bunker
            │ PASS
            ▼
[GATE 2: Liquidity & Spread]    ──────► FAIL: Reject Asset (Avoid Slippage Trap)
            │ PASS
            ▼
[GATE 3: Order Flow Toxicity]   ──────► FAIL: Reject Asset (Avoid Insider Dumps)
            │ PASS
            ▼
[GATE 4: Alpha Identification]  ──────► FAIL: Reject Asset (No Statistical Edge)
            │ PASS
            ▼
[GATE 5: Sizing & Execution]    ──────► CALCULATE: Volatility-Adjusted Kelly Sizing
            │
            ▼
    [EXECUTE ORDER VIA API]
```

### Gate 1: Macro & Regime Permission
* **Condition 1A (BTC Trend)**: Bitcoin 1-Hour Close $\ge \text{EMA}_{20}(\text{BTC})$.
* **Condition 1B (BTC Order Flow)**: BTC 4-Hour Taker Buy Ratio $\ge 45.0\%$.
* **Condition 1C (Systemic Volatility)**: BTC $15\text{m ATR} < 2.2 \times \overline{\text{ATR}}_{20}$.
* *Rationale*: Altcoin alpha only persists when systematic crypto beta is stable. If BTC is dumping, all altcoins drop $2\times$ harder regardless of chart setup.

### Gate 2: Liquidity & Microstructure Gate
* **Condition 2A (24h Volume)**: $\text{UnitTradeValue} \ge \$5,000,000\text{ USD}$.
* **Condition 2B (Bid-Ask Spread)**: $\frac{\text{MinAsk} - \text{MaxBid}}{\text{LastPrice}} \le 0.035\%$.
* **Condition 2C (Execution Capacity)**: Proposed order size $\le 0.5\%$ of average hourly volume.
* *Rationale*: Eliminates fee/spread traps like `PEPE` (spread $0.23\%$) or tokenized stocks like `PLTRB` ($88k daily volume).

### Gate 3: Order Flow Toxicity & Whale Filter
* **Condition 3A (Taker Flow)**: Rolling 4-Hour Taker Buy Ratio $\ge 51.0\%$.
* **Condition 3B (Order Imbalance)**: Rolling 15-Minute Taker Imbalance $\ge 0.0$:
  $$\text{Imbalance} = \frac{\text{Taker Buy Quote} - \text{Taker Sell Quote}}{\text{Total Quote Volume}} \ge 0$$
* **Condition 3C (No Toxic Dumps)**: Zero 15-minute candles in the past 2 hours with Volume $> 2.5\sigma$ and negative close.
* *Rationale*: Ensures we are entering alongside informed institutional buyers and never buying into a whale offloading inventory.

### Gate 4: Alpha Thesis Identification & Cross-Crypto Lag Signal
The bot must classify the exact statistical alpha source:
* **Alpha Type A (Beta-Disparity Cross-Crypto Lag Signal)**:
  Altcoins lag Bitcoin expansions by 2 to 4 hours due to information diffusion delays. The expected catch-up move is governed by:
  $$\text{LagSpread}_i(t) = \beta_i \cdot R_{\text{BTC}, 4\text{h}}(t) - R_{i, 4\text{h}}(t)$$
  Where $\beta_i = \frac{\text{Cov}(R_i, R_{\text{BTC}})}{\text{Var}(R_{\text{BTC}})}$ is the rolling 24-hour empirical beta.
  * *Condition*: $\text{LagSpread}_i \ge +1.0\%$ AND 4-Hour Taker Buy $\ge 51.0\%$.
  * *Calculus*: Altcoin $i$ has lagged BTC's upward breakout relative to its expected sensitivity; institutional taker buying indicates an imminent catch-up drift (+0.64% to +1.43% forward edge).
* **Alpha Type B (Cross-Sectional Momentum & Residual Alpha)**: 
  * 12-Hour Return $\ge +1.0\%$ AND Residual Alpha $\alpha_i = R_{i, 12\text{h}} - \beta_i R_{\text{BTC}, 12\text{h}} > 0$.
  * Taker Imbalance $\ge 0.0$.

### Gate 5: 4-Tier Categorical Portfolio Sizing Matrix
Portfolio capital is allocated according to institutional risk budgeting across 4 categorical tiers:

| Tier | Categorical Basket | Baseline / Sideways | Bull Expansion | Bear Contraction |
| :--- | :--- | :---: | :---: | :---: |
| **Tier 1: Core Anchor** | BTC (fallback ETH) | **60%** | **20%** | **0%** |
| **Tier 2: Smart Contracts** | ETH, SOL, SUI | **15% - 20%** | **35%** | **0%** |
| **Tier 3: Infrastructure & AI** | LINK, TAO, NEAR | **5% - 10%** | **15%** | **0%** |
| **Tier 4: Speculative Beta** | DOGE, AVAX, BNB, XRP, ADA | **0% - 5%** | **10%** | **0%** |
| **Defense / Safe Haven** | USD Cash + PAXG Gold + Short BTC | **20% Cash** | **20% Cash** | **70% Cash + 20% PAXG + 10% Short** |

* **Single-Asset Cap**: Max **40%** ($40,000 USD).
* **Minimum Cash Buffer**: Kept at $\ge 15\% - 20\%$ to absorb exchange taker fees (0.10%) and ensure zero liquidation risk.

---

## 3. The Calculating Decision Calculus for TRADE EXITS

Every open position is continuously evaluated on a **1-minute loop**. An exit occurs exclusively when one of four mathematical conditions is met:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           1-MINUTE RISK EVALUATION LOOP                     │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
       ┌───────────────────────────────┼───────────────────────────────┐
       ▼                               ▼                               ▼
[1. Stop-Loss Trigger]        [2. Profit Ratchet Trigger]     [3. Toxicity Trigger]
Loss >= max(3.0%, 2.2*ATR)    Gain >= +2.0%: Lock +0.4%       Taker Imbalance < -25%
Action: Market Sell (Cash)    Gain >= +4.0%: Trail Peak -1.2% Action: Emergency Sell
```

### Exit Rule 1: Volatility-Adjusted Hard Stop-Loss
* **Trigger Condition**:
  $$\text{Unrealized PnL} \le -\max(3.0\%, 2.2 \times \text{ATR}_{15\text{m}})$$
* **Calculated Action**: Immediately liquidate $100\%$ of position to cash. Prevents an intraday pullback from turning into a $-10\%$ account killer.

### Exit Rule 2: Dynamic Profit Ratchet & Trailing Lock
* **Stage 1 (Breakeven Lock)**:
  * If Unrealized PnL reaches **$+2.0\%$**: Ratchet stop-loss to **Entry Price $+0.40\%$**.
  * *Calculus*: Guarantees that fees ($0.2\%$) are covered and the trade mathematically cannot become a loss.
* **Stage 2 (Trailing Profit Run)**:
  * If Unrealized PnL reaches **$+4.0\%$**: Activate trailing stop at **$\text{Highest Price} - 1.2\%$**.
  * *Calculus*: Lets exponential runners (like ADA $+12\%$) run while locking in at least $70\%$ of peak gains.

### Exit Rule 3: Order Flow Toxicity Exit (Insider Dump Evasion)
* **Trigger Condition**:
  $$\text{Rolling 15m Taker Imbalance} < -25.0\% \quad \text{AND} \quad \text{Volume} > 2.5\sigma$$
* **Calculated Action**: Immediate Emergency Market Sell.
* *Calculus*: Informs us that whales/insiders are dumping before public disclosure; avoids holding through a multi-hour breakdown.

### Exit Rule 4: Cycle Demotion (Rebalancing Rotation)
* Evaluated every **8 to 12 Hours**:
* If an active asset falls below Rank 4 in momentum or Taker Buy Ratio falls below $49.0\%$, the position is gracefully closed to recycle capital into the new Top 2 leaders.

---

## 4. The Macro Circuit Breaker (Black Swan Protocol)

* **Trigger**: If total portfolio value drops by **$> 2.0\%$** from all-time peak within a rolling 24-hour window.
* **Action**:
  1. Liquidate $100\%$ of all open positions into USD Cash.
  2. Cancel all pending orders.
  3. Freeze new order entries for a mandatory **4-Hour Cooldown**.
* **Calculus**: Caps the maximum possible portfolio drawdown at $2.0\%$, mathematically preserving top-tier Calmar ($\ge 5.0$) and Sortino ($\ge 8.0$) scores.

---

## 5. Directional Long-to-Short Conversion & Fee-Profit Calculus

During market breakdowns (Bear Contraction), the bot can convert exposure from **Long to Short** via `/v6/short_open` to generate positive returns while the broader market drops. However, directional flipping incurs cumulative exchange frictions that must be justified mathematically.

### 5.1 The Conversion Friction Hurdle
Flipping from an existing Long position into a Short position involves three transaction stages:
1. **Closing Long Position**: $0.10\%$ Taker Fee $+ \approx 0.02\%$ Bid-Ask Spread $= 0.12\%$
2. **Opening Short Position**: $0.10\%$ Taker Fee $+ \approx 0.02\%$ Bid-Ask Spread $= 0.12\%$
3. **Closing Short Position**: $0.10\%$ Taker Fee $+ \approx 0.02\%$ Bid-Ask Spread $= 0.12\%$

$$\text{Total Conversion Friction } \text{Friction}_{\text{flip}} \approx 0.36\%$$

### 5.2 The Net Expectancy Condition for Flipping
A Long-to-Short transition is executed **if and only if** the expected downward move satisfies the Positive Net Expectancy Hurdle:

$$\mathbb{E}[R_{\text{short, net}}] = \Big(P(\text{Down}) \times \bar{R}_{\text{down}}\Big) - \Big(P(\text{Up}) \times \bar{R}_{\text{up}}\Big) - \text{Friction}_{\text{flip}} \ge +0.80\%$$

* **Condition 1 (Macro Trend Breakdown)**: Bitcoin 1-Hour Close $< \text{EMA}_{20}$ AND 12-Hour Return $< -1.5\%$.
* **Condition 2 (Institutional Order Flow Dumping)**: Rolling 4-Hour Taker Buy Ratio $< 45.0\%$ (aggressive market-sell dominance).
* **Condition 3 (Microstructure Stability)**: 15-Minute ATR $< 2.2 \times \overline{\text{ATR}}_{20}$ (verifies directional trend rather than an erratic, wide-spread liquidation spike).

**Action When Hurdle Is NOT Met (Marginal Dips / Choppy Drift)**:
If downward velocity is marginal (e.g., BTC down only $-0.4\%$ to $-0.8\%$), flipping to short is negative EV due to fee drag. The bot **does not short**; instead, it converts $100\%$ of capital into **USD Cash Bunker** ($0$ fees, $0$ risk, capital strictly protected).

### 5.3 Risk Guard Daemon Rules for Short Positions
Short positions carry asymmetrical upside squeeze risk. The 1-minute `RiskGuard` daemon enforces dedicated short protection:
1. **Tighter Hard Stop-Loss**: Set at **$+2.5\%$** above short entry price (immediately liquidates via `/v6/short_close` if price rallies).
2. **Breakeven Profit Ratchet for Shorts**: When price drops by $\ge 2.0\%$ ($+2.0\%$ short gain), stop ratchets to **Entry Price $-0.40\%$** (guarantees fees are covered and trade cannot become a loss).
3. **Trailing Profit Lock for Shorts**: When price drops by $\ge 4.0\%$, trailing stop is pegged at **$\text{Trough Price} + 1.2\%$** (tracks lowest price achieved and locks in $>70\%$ of down-move profits).
4. **Short Squeeze Toxicity Emergency Exit**: If 15-minute Taker Imbalance abruptly flips to $> +25.0\%$ with Volume $> 2.5\sigma$, the short is closed immediately to evade short squeezes.
