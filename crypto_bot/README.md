# Crypto Demo Trading Bot

An autonomous Python crypto trading bot that runs on the Binance testnet.
It fetches live market data, calculates technical indicators, detects breakout/continuation setups,
sizes positions using ATR-based risk management, executes demo orders, and monitors open positions.

## Project Structure

```
crypto_bot/
├── app.py                   # Main entry point and loop
├── requirements.txt         # Python dependencies
├── .env.example             # Environment variable template
├── config/
│   └── settings.py          # Loads and validates all settings from .env
├── exchange/
│   └── connector.py         # Binance testnet connection and balance fetch
├── data/
│   └── market_data.py       # OHLCV candle fetching
├── features/
│   └── indicators.py        # EMA20, EMA50, RSI14, ATR14, avg volume, swing hi/lo
├── strategy/
│   └── signal.py            # 1h trend detection + 15m setup detection
├── risk/
│   └── calculator.py        # ATR-based SL/TP, position sizing
├── execution/
│   └── order_executor.py    # Demo market order placement via ccxt
├── monitoring/
│   └── position_monitor.py  # Open trade SL/TP monitoring
├── storage/
│   └── trade_store.py       # SQLite (open trades) + CSV (closed trades)
└── logs/
    └── logger.py            # Rotating file + console logger
```

## Requirements

- Python 3.11 or higher
- A Binance testnet account with API keys (see below)

## Setup

### 1. Install dependencies

```bash
cd crypto_bot
pip install -r requirements.txt
```

### 2. Configure environment variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

```
BINANCE_API_KEY=your_testnet_api_key
BINANCE_API_SECRET=your_testnet_api_secret
SYMBOLS=BTCUSDT,ETHUSDT
RISK_PERCENT=1.0
REWARD_TO_RISK=2.0
STARTING_DEMO_BALANCE_USDT=10000.0
LOOP_INTERVAL_SECONDS=900
```

### 3. Get Binance testnet credentials

1. Go to [https://testnet.binance.vision/](https://testnet.binance.vision/)
2. Log in with your GitHub account
3. Generate API Key and Secret
4. Paste them into your `.env`

Testnet funds are automatically provided. You do not need real money.

### 4. Run the bot

```bash
cd crypto_bot
python app.py
```

The bot will:
1. Connect to Binance testnet and verify credentials
2. Fetch your account USDT balance
3. Enter a continuous loop every `LOOP_INTERVAL_SECONDS` (default 15 minutes):
   - Monitor any open positions for SL/TP hits
   - Fetch fresh OHLCV candles for all symbols
   - Calculate indicators (EMA20/50, RSI14, ATR14, avg volume, swing high/low)
   - Detect breakout/continuation setups on 15m (trend-confirmed on 1h)
   - Size and execute demo orders when a valid setup is found
   - Log all activity to console and `logs/bot.log`

## Strategy

- **Trend filter (1h):** EMA20 > EMA50 → bullish trend; EMA20 < EMA50 → bearish trend
- **Long entry (15m):** Close breaks above recent 20-bar swing high with above-average volume during bullish 1h trend
- **Short entry (15m):** Close breaks below recent 20-bar swing low with above-average volume during bearish 1h trend
- **Stop loss:** Entry price ± (ATR × 1.5)
- **Take profit:** Entry price ± (stop distance × reward_to_risk)
- **One trade per symbol at a time**

## Storage

- **Open trades:** stored in `storage/trades.db` (SQLite)
- **Closed trades:** appended to `storage/closed_trades.csv`
- **Logs:** written to `logs/bot.log` (rotates at 5MB, keeps 3 backups)

## Exporting from Replit and running locally

1. In Replit, open the Files panel and download the `crypto_bot/` folder as a zip, or use the Shell:
   ```bash
   zip -r crypto_bot_export.zip crypto_bot/
   ```
2. Download `crypto_bot_export.zip` from the Replit Files panel
3. On your local machine:
   ```bash
   unzip crypto_bot_export.zip
   cd crypto_bot
   pip install -r requirements.txt
   cp .env.example .env   # fill in your credentials
   python app.py
   ```

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `BINANCE_API_KEY` | Yes | — | Binance testnet API key |
| `BINANCE_API_SECRET` | Yes | — | Binance testnet API secret |
| `SYMBOLS` | No | `BTCUSDT,ETHUSDT` | Comma-separated trading pairs |
| `RISK_PERCENT` | No | `1.0` | % of balance to risk per trade |
| `REWARD_TO_RISK` | No | `2.0` | Take profit multiplier vs stop distance |
| `STARTING_DEMO_BALANCE_USDT` | No | `10000.0` | Fallback balance if API returns 0 |
| `LOOP_INTERVAL_SECONDS` | No | `900` | Seconds between each bot cycle |

## Notes

- This bot is for demo/testnet use only. No real funds are at risk.
- Do not deploy with real Binance credentials without adding additional safeguards.
- ATR-zero rejection, duplicate trade prevention, and balance checks are all active.
