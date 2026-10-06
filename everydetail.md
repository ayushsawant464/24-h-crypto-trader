​What You’ll Do

​Design and implement quantitative trading strategies

​Deploy live strategies with virtual portfolio in financial markets

​Compete against top teams across HK, AU, and IN

​Present your approach to industry professionals
Participants will design, build, and deploy live quantitative trading strategies, compete in real-time markets
​Competition Structure

​Evaluation Criteria (In Order):

​Evaluation for finalist teams will be conducted in the following stages:

​Screen 1: Rule Compliance (Mandatory Requirement)

​Teams that violate any of the following rules will not be selected as finalists:

​Trade Log Integrity

​Bots must demonstrate consistent, autonomous trade execution aligned with their declared strategy.

​Commit History Transparency

​All strategy updates must have a consistent and traceable commit history. No traces of manually called APIs.

​Screen 2: Portfolio Returns (Leaderboard Qualification)

​After screen 1 disqualification, the following Top 20 teams on leaderboard from each region, ranked by portfolio return, will advance for further evaluation.

​Portfolio Return is calculated as: (Final Portfolio Value – Initial Portfolio Value) / Initial Portfolio Value

​Screen 3: Composite Risk‑Adjusted Performance Score

​Qualified teams will be evaluated using a composite risk-adjusted performance score based on three key metrics.

​All underlying data, calculations, and results will be transparently published on the Finale Day.

​Composite Score Formula:

​0.4 × Sortino Ratio

​0.3 × Sharpe Ratio

​0.3 × Calmar Ratio

​Screen 4: Code & Strategy Review

​Each shortlisted team’s repository and strategy implementation will undergo technical review.

​Evaluation criteria include:

​Clear and coherent implementation of trading strategy logic

​Clean, well-structured, and properly maintained code repository

​Fully runnable continuously with compatibility on the Roostoo platform


​Hackathon Problem Statement

​Description:

​Develop an AI-driven trading algorithm, or a traditional quantitative rule-based algorithm, or any creative hybrid strategies to compete on Roostoo’s real-time mock exchange backend.

​Using the APIs provided in the Roostoo API Documents, your task is to design a trading bot that autonomously makes buy, hold, and sell decisions—without any manual intervention—by interacting with the Roostoo backend exchange engine via POST and GET API requests.

​The objective is to maximize portfolio returns while minimizing risk, as measured by portfolio return, Sortino Ratio, Sharpe Ratio, and Calmar Ratio.

​Requirements:

​Strategies and bot usage are open-ended; there are no limitations or predefined rules. You may use any approach, including LLM models, reinforcement learning algorithms such as PPO agents, traditional trading strategies, or even your own custom solutions built from scratch.

​You are welcome to use any data sources. Roostoo platform data is also freely available via API GET requests, as detailed in the documentation. Please note that Roostoo will only cover cloud server costs; any additional data source costs (such as LLM API calls) are not covered.

​Roostoo will display bot names on the Roostoo app, creating a live competition leaderboard among teams.
​We strongly recommend that you also record all trades and performance logs of your bot internally, and keep track of the success or failure status of each API request.

​The competition will run for on AWS cloud infrastructure (provisioned by Roostoo) during the hackathon.

​You are required to deploy your bot on an AWS VM and ensure it executes trades automatically on the Roostoo platform.

​Rules and Constraints

​No high-frequency trading, market-making, or arbitrage strategies are allowed. Excessive server requests will result in failed API responses.

​Only spot trading (1x long and short) is permitted on all available financial assets on Roostoo, no leverage.

​Each team will be given $100,000 mock portfolio to manage.

​Each executed order takes 0.1% commission fee for taker order (market order) and 0.05% for maker order (limit order).

​Teams must submit their repositories as open-source for code validation.

​Each team will be provided with an AWS sub-account to launch an EC2 instance for hosting your bot on the cloud.

​Resources
​Roostoo API - 
/
roostoo/Roostoo-API-Documents

https://github.com/roostoo/Roostoo-API-Documents?utm_source=luma
# Roostoo API Usage

### Q19. How do I authenticate with Roostoo API?

**Ans:**

- Use API Key
- Sign payload using HMAC SHA256
- Include timestamp
- Send `RST-API-KEY` and `MSG-SIGNATURE` headers

### Q20. What are the main API endpoints?

- `/v3/serverTime`
- `/v3/exchangeInfo`
- `/v3/ticker`
- `/v3/balance`
- `/v3/place_order`
- `/v3/query_order`
- `/v3/cancel_order`

### I get “HTTP: Max retries exceeded” error. What should I do?

**Ans:**

- Implement retry with exponential backoff
- Reduce request frequency
- Catch exceptions properly

###
### How do we monitor portfolio during testing?

**Ans:** Use API:

- `/v3/balance`
- `/v3/query_order`

Frontend dashboard not available during testing.

###
Recommended structure:
Best practices:

- Use `.env` for API keys
- Add clear README
- Use Git branches (main/dev)
- Tag final submission version
- Keep it reproducible
### Q41. What’s best practice for repository management?

Recommended structure:

```
bot/
  strategy/
  execution/
  data/
  config/
  logs/
tests/
requirements.txt
Dockerfile
README.md
```
### Should teams log their trades?

Yes — strongly recommended.

Minimum logging:

- Timestamp
- Symbol
- Side
- Price
- Quantity
- Order ID
- API response

Optional:

- PnL
- Signal reason
- Strategy state
### How should bots log activity?

Options:

- Local file logging
- CloudWatch logs
- Database (SQLite/Postgres)
- CSV export for evaluation

###
### What should be included in the README file of repository?

Ans: Your README should clearly explain how your bot works and how to run it. Judges should be able to understand and reproduce your project easily.

Recommended README Structure (feel free to create your own structure too):

1. **Project** **Overview**
    - Short description of your strategy
    - High-level idea (e.g., momentum, mean reversion, ML-based, arbitrage, etc.)
    - Key features
2. **Architecture**
    - System design diagram (optional but recommended)
    - Components (data module, strategy module, execution module, logging module)
    - Tech stack used
3. **Strategy Explanation**
    - Entry conditions
    - Exit conditions
    - Risk management rules
    - Position sizing logic
    - Any assumptions made
4. **Setup instructions & How to run bot**
Common Technical Errors & Solutions
Issue	Likely Cause	Fix
422 Error	Invalid params	Check API docs
Max retries exceeded	Too many requests	Reduce frequency
Negative balance -0.01	Rounding	Ignore
Cannot see IAM role	Wrong login method	Use invitation email
Instance error	Not fully provisioned	Wait + refresh

## 1. Binance Public Data *(Highly Recommended)*

**Pricing:** 100% Free

**Link:** Binance Vision Data

**Overview:**
Binance Vision is the official public data repository for the Binance exchange. It allows developers and researchers to bypass rate limits by directly downloading bulk archives of Binance's historical market data.

**Key Features & Data Types:**

- **Complete Binance History:** Provides tick-level and aggregated data for all Spot, USD-Margined Futures, and COIN-Margined Futures traded on Binance.
- **Klines (Candlesticks):** Available in intervals from 1-second (`1s`) up to 1-month (`1mo`).
- **Trade Data:** Raw trades and Aggregated Trades (`AggTrades`).
- **No Authentication Needed:** You do not need a Binance account or an API key to download this data.

**Delivery Methods:**

- Direct `.zip` downloads via the web browser.
- Programmatic downloads using `wget` or `curl` commands.
- Open-source Python packages (like `binance-historical-data` on PyPI) or GitHub shell scripts that automate fetching daily and monthly data dumps.

**Best For:**
Heavy backtesting, data science, and training Machine Learning models where the team needs massive amounts of highly accurate historical data for free, and is okay with relying strictly on Binance's market liquidity.


