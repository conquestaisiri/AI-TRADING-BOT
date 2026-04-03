# Crypto Demo Trading Bot

An autonomous modular Python trading bot running on the **Binance Futures Testnet**.

Fetches live market data, calculates technical indicators, detects breakout/continuation setups,
manages risk with ATR-based sizing, executes demo futures orders, monitors open positions,
and logs every decision with full context.

> **This bot uses the Futures testnet, not the Spot testnet.**
> The Futures testnet supports both long and short positions correctly.
> Spot cannot short — that's why Futures is required.

---

## Project Structure

```
crypto_bot/
├── app.py                      # Main entry point and autonomous loop
├── requirements.txt            # Python dependencies
├── .env.example                # Environment variable template
├── .gitignore
│
├── config/
│   └── settings.py             # All settings loaded from .env, validated at startup
│
├── exchange/
│   └── connector.py            # Binance Futures testnet connection + balance
│
├── data/
│   └── market_data.py          # OHLCV candle fetching for all symbols and timeframes
│
├── features/
│   └── indicators.py           # EMA20/50, RSI14, ATR14, avg volume, swing hi/lo
│                               # + build_feature_summary() for future AI layer
│
├── strategy/
│   └── signal.py               # 1h trend + 15m breakout detection with filters
│                               # Returns typed Setup or RejectionRecord with reason
│
├── risk/
│   └── calculator.py           # ATR-based SL/TP, position sizing, balance cap
│
├── execution/
│   └── order_executor.py       # Demo futures market order via ccxt
│
├── monitoring/
│   └── position_monitor.py     # SL/TP polling, unrealised PnL logging, close flow
│
├── storage/
│   ├── trade_store.py          # SQLite (open trades) + CSV (closed trades audit log)
│   ├── trades.db               # Created on first run
│   └── closed_trades.csv       # Appended on each trade close
│
└── logs/
    ├── logger.py               # Rotating file + console logger
    └── bot.log                 # Created on first run (rotates at 5MB)
```

---

## Requirements

- Python 3.11 or higher
- Binance Futures Testnet account with API credentials

---

## Setup

### 1. Install Python dependencies

```bash
cd crypto_bot
pip3 install -r requirements.txt
```

### 2. Get Binance Futures Testnet credentials

1. Go to **[https://testnet.binancefuture.com](https://testnet.binancefuture.com)**
2. Log in with your GitHub account
3. Go to **API Key** section and generate a key pair
4. Copy the API Key and Secret

> **Do not use credentials from testnet.binance.vision** — that is the Spot testnet
> and it cannot short. You need the Futures testnet specifically.

### 3. Configure your environment

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:

```
BINANCE_API_KEY=your_futures_testnet_api_key
BINANCE_API_SECRET=your_futures_testnet_api_secret
EXCHANGE_TYPE=future
SYMBOLS=BTCUSDT,ETHUSDT
RISK_PERCENT=1.0
REWARD_TO_RISK=2.0
ATR_STOP_MULTIPLIER=1.5
STARTING_DEMO_BALANCE_USDT=10000.0
```

### 4. Run the bot

```bash
cd crypto_bot
python3 app.py
```

---

## What the bot does each cycle

1. **Monitor** — checks all open positions for stop loss / take profit hits
2. **Balance refresh** — fetches current USDT balance from testnet
3. **Data fetch** — downloads fresh 1h and 15m candles for all symbols
4. **Indicator calculation**:
   - EMA20, EMA50, EMA spread % (trend strength)
   - RSI14
   - ATR14, ATR as % of price (volatility check)
   - Rolling avg volume
   - **Shifted swing high/low** — prior N bars only (current candle excluded to prevent self-reference)
5. **Setup detection** — with all filters active:
   - 1h trend must be clear (EMA spread above minimum)
   - 15m close must break the *prior* swing level
   - Volume must exceed average
   - RSI must not be in extended/overbought zone
   - ATR must exceed minimum % (no choppy/dead market entries)
   - No existing open trade for that symbol
   - No loss cooldown active
6. **Risk calculation** — ATR-based stop, configurable R:R, position cap
7. **Order execution** — futures market order on testnet
8. **Log** — every decision logged with full context

---

## Strategy

| Rule | Long | Short |
|---|---|---|
| 1h trend | EMA20 > EMA50 | EMA20 < EMA50 |
| Trend strength | EMA spread >= `EMA_MIN_SPREAD_PCT`% | Same |
| Entry trigger | 15m close > prior N-bar swing high | 15m close < prior N-bar swing low |
| Volume | Current volume > rolling avg | Same |
| RSI filter | RSI < `RSI_OVERBOUGHT` (72) | RSI > `RSI_OVERSOLD` (28) |
| Market condition | ATR >= `ATR_MIN_PCT`% of price | Same |
| Duplicate check | No open trade for symbol | Same |
| Loss cooldown | Pauses after N consecutive SL hits | Same |

---

## Risk Management

- **Stop loss**: `entry ± (ATR × ATR_STOP_MULTIPLIER)`
- **Take profit**: `entry ± (stop_distance × REWARD_TO_RISK)`
- **Position size**: `(balance × RISK_PERCENT%) / stop_distance`
- **Hard cap**: position cost cannot exceed available balance
- **Zero ATR rejection**: rejects trade if ATR is zero or market is too quiet

---

## Storage

| File | Purpose |
|---|---|
| `storage/trades.db` | SQLite — open trade state, crash-persistent |
| `storage/closed_trades.csv` | CSV — all closed trades, human-readable audit log |
| `logs/bot.log` | Rotating log file (5MB max, 3 backups) |

---

## Important note on SL/TP

Stop loss and take profit are **managed app-side** (not as exchange orders).
The bot polls the current price each cycle and closes positions internally.

This means:
- **The bot must keep running** to protect open positions
- If the bot crashes mid-trade, the exchange order stays open without protection
- A future version can add exchange-native bracket orders for true protection

For demo/testnet purposes this is acceptable. For live trading with real money,
exchange-native protective orders are strongly recommended.

---

## AI Layer (planned)

The feature engine (`features/indicators.py`) already produces a `build_feature_summary()` output:

```python
{
  "symbol": "BTCUSDT",
  "timeframe": "15m",
  "trend": "bullish",
  "ema_spread_pct": 0.45,
  "rsi": 61.3,
  "rsi_zone": "bullish_momentum",
  "atr_pct": 1.2,
  "volume_spike": true,
  "swing_high": 42800.0,
  "swing_low": 41200.0,
  ...
}
```

This structured dict is designed to be passed directly into an AI model (Qwen, DeepSeek, Llama)
for regime classification and trade scoring — without any restructuring needed.

---

## Exporting from Replit and running locally

### From Replit

```bash
zip -r crypto_bot_export.zip crypto_bot/
```

Download `crypto_bot_export.zip` from the Replit Files panel.

### On your local machine

```bash
unzip crypto_bot_export.zip
cd crypto_bot
pip3 install -r requirements.txt
cp .env.example .env    # fill in your credentials
python3 app.py
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `BINANCE_API_KEY` | **Yes** | — | Futures testnet API key |
| `BINANCE_API_SECRET` | **Yes** | — | Futures testnet API secret |
| `EXCHANGE_TYPE` | No | `future` | Must be `future` for long/short support |
| `SYMBOLS` | No | `BTCUSDT,ETHUSDT` | Comma-separated futures pairs |
| `RISK_PERCENT` | No | `1.0` | % of balance risked per trade |
| `REWARD_TO_RISK` | No | `2.0` | TP distance as multiple of SL distance |
| `ATR_STOP_MULTIPLIER` | No | `1.5` | Stop = entry ± (ATR × this) |
| `STARTING_DEMO_BALANCE_USDT` | No | `10000.0` | Fallback balance if API returns 0 |
| `RSI_OVERBOUGHT` | No | `72.0` | Max RSI for long entries |
| `RSI_OVERSOLD` | No | `28.0` | Min RSI for short entries |
| `EMA_MIN_SPREAD_PCT` | No | `0.1` | Min EMA20/50 gap % to confirm trend |
| `ATR_MIN_PCT` | No | `0.05` | Min ATR % of price (chop filter) |
| `LOSS_COOLDOWN_COUNT` | No | `2` | Consecutive SL hits before cooldown |
| `LOSS_COOLDOWN_LOOPS` | No | `2` | Loops to wait during cooldown |
| `OHLCV_LIMIT` | No | `300` | Candles per fetch request |
| `LOOP_INTERVAL_SECONDS` | No | `900` | Seconds between cycles (15 min) |
