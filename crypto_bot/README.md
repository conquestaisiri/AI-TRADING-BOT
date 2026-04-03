# Crypto Demo Trading Bot

An autonomous modular Python trading bot running on the **Binance Futures Testnet**.

Fetches live market data, classifies the market regime, validates breakout quality,
manages risk with ATR-based sizing, executes demo futures orders, monitors open positions,
enforces cooldown and frequency limits, and logs every decision with full context.

> **This bot uses the Futures testnet, not the Spot testnet.**
> Go to [https://testnet.binancefuture.com](https://testnet.binancefuture.com) for credentials.
> The Spot testnet cannot short. The Futures testnet supports both long and short correctly.

---

## Project Structure

```
crypto_bot/
├── app.py                      # Main entry point and autonomous loop
├── requirements.txt            # Python dependencies
├── .env.example                # Environment variable template
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
│   └── indicators.py           # EMA20/50/slopes, RSI14, ATR14/MA, swing hi/lo (shifted),
│                               # candle body/wick metrics, overextension metric
│                               # + build_feature_summary() for AI layer hook
│
├── strategy/
│   ├── signal.py               # 7-stage evaluation engine → SignalEvaluation objects
│   └── regime.py               # Rule-based regime classifier (trending/ranging/choppy)
│
├── risk/
│   └── calculator.py           # ATR-based SL/TP, position sizing, balance cap
│
├── execution/
│   └── order_executor.py       # Demo futures market order via ccxt using SignalEvaluation
│
├── monitoring/
│   └── position_monitor.py     # SL/TP polling, unrealised PnL logging, close flow
│
├── storage/
│   ├── trade_store.py          # SQLite (open trades) + CSV (closed trades) + cooldown queries
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
- Binance Futures Testnet account (free, sign up with GitHub)

---

## Setup

### 1. Install dependencies

```bash
cd crypto_bot
pip3 install -r requirements.txt
```

### 2. Get Binance Futures Testnet credentials

1. Go to [https://testnet.binancefuture.com](https://testnet.binancefuture.com)
2. Log in with your GitHub account
3. Go to **API Key** → generate a new key pair
4. Copy the API Key and Secret

> Do not use credentials from `testnet.binance.vision` — that is the Spot testnet
> and does not support shorting.

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in your credentials
```

### 4. Run the bot

```bash
python3 app.py
```

---

## What happens each cycle

1. **Monitor** — checks all open positions for stop loss / take profit hits
2. **Balance refresh** — fetches current USDT balance from testnet
3. **Data fetch** — downloads 1h and 15m candles for all configured symbols
4. **Indicator calculation** — EMA slopes, ATR MA, swing levels, candle metrics, overextension metric
5. **Feature summaries** — logged at DEBUG level, ready for future AI layer
6. **7-stage signal evaluation** per symbol:

| Stage | What is checked |
|---|---|
| 1 | Data sufficiency — enough valid candles and indicator data |
| 2 | 1h trend determination — EMA20 vs EMA50 alignment and spread |
| 3 | Regime classification — trending / ranging / choppy |
| 4 | Breakout candidate — 15m close beyond shifted swing level |
| 5 | Breakout quality — close buffer, volume, body size, wick rejection |
| 6 | Overextension, RSI extremes, cooldown, trade frequency limit |
| 7 | Risk calculation, final approval, full signal construction |

7. **Order execution** — approved signals → futures market orders on testnet
8. **Full logging** — every decision, rejection reason, and trade detail logged

---

## Market Regime Filter

The bot classifies the market before any breakout check. Only `"trending"` markets allow trades.

Regime score is computed from 4 rule-based factors (each worth up to 0.25 → max score 1.0):

| Factor | Contributes 0.25 when... |
|---|---|
| EMA spread strength | EMA20/50 separation >= 0.60% of price |
| EMA slope direction | EMA20 slope is positive (bullish) or negative (bearish) in expected direction |
| ATR expansion | Current ATR > 14-period ATR moving average |
| Price-EMA alignment | Price is on the correct side of both EMA20 and EMA50 |

Labels:
- `score >= REGIME_MIN_TREND_SCORE` → **trending** (trade allowed)
- `score >= 0.30 and < threshold` → **ranging** (no trade)
- `score < 0.30` → **choppy** (no trade)

Rejection log: `REGIME_UNFAVORABLE: Regime=ranging score=0.38 (need>=0.50 for 'trending')...`

---

## Overextension Protection

Three independent overextension checks prevent late, stretched entries:

1. **Candle body too large** (`MAX_BODY_ATR_RATIO`): Rejects if the breakout candle body
   is more than N × ATR. Catches cases where you are chasing a giant candle.

2. **Price too far from EMA20** (`MAX_DISTANCE_FROM_EMA_ATR_RATIO`): Rejects if price
   is more than N × ATR away from EMA20. Catches entries late into an extended move.

3. **RSI extremes**: Rejects longs if RSI > `RSI_OVERBOUGHT`, shorts if RSI < `RSI_OVERSOLD`.

All overextension metrics are included in the `SignalEvaluation` object for logging and future AI input.

---

## Breakout Quality Validation

Not every close beyond a swing level is a valid breakout. The bot checks 4 quality conditions:

| Check | Purpose |
|---|---|
| Close buffer | Close must exceed the breakout level by >= `BREAKOUT_CLOSE_BUFFER_RATIO × ATR` — prevents wick-only fake breaks |
| Volume confirmation | Volume must be >= `VOLUME_RATIO_THRESHOLD × avg_volume` — confirms real demand/supply |
| Body-to-range ratio | Candle body must be >= `MIN_BODY_TO_RANGE_RATIO × candle range` — rejects wick-dominated candles |
| Rejection wick check | Rejects if opposing wick is > 2× the candle body — signals price reversal at the level |

Swing levels use `.shift(1)` before rolling so the current candle's own high/low is never
included in the level it must break. This prevents self-reference.

---

## Cooldown After Trades

After any trade closes, new entries on that symbol are blocked for a configurable number
of completed 15m candles. Cooldown is persistent — measured from the actual `closed_at`
timestamp in SQLite, not from loop count.

- After a **loss** (stop hit): wait `LOSS_COOLDOWN_CANDLES` × 15m candles
- After a **win** (target hit): wait `WIN_COOLDOWN_CANDLES` × 15m candles

This prevents revenge trading after a stop-out and ensures the bot lets positions breathe
after a win before re-entering.

Rejection log: `COOLDOWN_ACTIVE: Symbol in cooldown after last loss. 2 more 15m candle(s) required.`

---

## Trade Frequency Limiter

Three frequency controls prevent overtrading:

| Setting | Default | Purpose |
|---|---|---|
| `MAX_TRADES_PER_WINDOW` | 3 | Max trades per symbol within the rolling window |
| `TRADE_WINDOW_MINUTES` | 480 (8h) | Rolling window size for trade count |
| `MIN_ENTRY_GAP_MINUTES` | 60 | Minimum minutes between consecutive entries |

Rejection log: `FREQUENCY_LIMIT: Only 25.3min since last entry — minimum gap is 60min.`

---

## Risk Management

| Parameter | Description |
|---|---|
| Stop loss | `entry ± (ATR × ATR_STOP_MULTIPLIER)` |
| Take profit | `entry ± (stop_distance × REWARD_TO_RISK)` |
| Position size | `(balance × RISK_PERCENT%) / stop_distance` |
| Hard cap | Position cost cannot exceed available balance |

---

## Storage

| File | Purpose |
|---|---|
| `storage/trades.db` | SQLite — open trade state, crash-persistent |
| `storage/closed_trades.csv` | CSV — all closed trades, human-readable audit log |
| `logs/bot.log` | Rotating log (5MB max, 3 backups) |

The trade store exposes query methods for the strategy engine:
- `get_last_closed_trade(symbol)` — used for cooldown checks
- `get_recent_entry_times(symbol, since)` — used for frequency limiting
- `get_recent_closed_trades(symbol, since)` — used for loss analysis

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `BINANCE_API_KEY` | **required** | Futures testnet API key |
| `BINANCE_API_SECRET` | **required** | Futures testnet API secret |
| `EXCHANGE_TYPE` | `future` | Must be `future` for long/short |
| `SYMBOLS` | `BTCUSDT,ETHUSDT` | Comma-separated futures pairs |
| `RISK_PERCENT` | `1.0` | % of balance risked per trade |
| `REWARD_TO_RISK` | `2.0` | TP as multiple of stop distance |
| `ATR_STOP_MULTIPLIER` | `1.5` | Stop = entry ± (ATR × this) |
| `STARTING_DEMO_BALANCE_USDT` | `10000.0` | Fallback if API returns 0 |
| `REGIME_MIN_TREND_SCORE` | `0.50` | Min regime score to allow trade |
| `VOLUME_RATIO_THRESHOLD` | `1.5` | Min volume vs avg volume |
| `MAX_BODY_ATR_RATIO` | `2.0` | Max candle body in ATR units |
| `MAX_DISTANCE_FROM_EMA_ATR_RATIO` | `3.0` | Max price distance from EMA20 in ATR |
| `BREAKOUT_CLOSE_BUFFER_RATIO` | `0.10` | Min close buffer beyond level in ATR |
| `MIN_BODY_TO_RANGE_RATIO` | `0.40` | Min body as fraction of candle range |
| `RSI_OVERBOUGHT` | `72.0` | Max RSI for long entries |
| `RSI_OVERSOLD` | `28.0` | Min RSI for short entries |
| `EMA_MIN_SPREAD_PCT` | `0.10` | Min EMA spread % for trend confirmation |
| `ATR_MIN_PCT` | `0.05` | Min ATR % (below = choppy market) |
| `LOSS_COOLDOWN_CANDLES` | `3` | 15m candles to wait after a loss |
| `WIN_COOLDOWN_CANDLES` | `1` | 15m candles to wait after a win |
| `MAX_TRADES_PER_WINDOW` | `3` | Max trades per rolling window |
| `TRADE_WINDOW_MINUTES` | `480` | Rolling window size in minutes |
| `MIN_ENTRY_GAP_MINUTES` | `60` | Min minutes between entries |
| `OHLCV_LIMIT` | `300` | Candles per fetch |
| `LOOP_INTERVAL_SECONDS` | `900` | Seconds between cycles |

---

## Stop Note

SL and TP are monitored app-side (price polling). The bot must stay running to protect
open positions. If the bot crashes mid-trade, the open position has no protection.
For live/real-money use, exchange-native bracket orders are strongly recommended instead.

---

## AI Layer Hook

`build_feature_summary()` in `features/indicators.py` returns a structured dict from
each enriched candle, ready to pass directly to any AI model for regime scoring or
trade quality assessment — no restructuring needed.

```python
{
  "symbol": "BTCUSDT", "timeframe": "15m", "trend": "bullish",
  "regime_label": "trending",  # from classify_regime()
  "ema_spread_pct": 0.72, "ema_fast_slope_pct": 0.031,
  "rsi": 61.3, "rsi_zone": "bullish_momentum",
  "atr_pct": 1.2, "atr_expanding": True,
  "volume_ratio": 2.3, "dist_from_ema_fast_atr": 1.8,
  "body_to_range": 0.67, ...
}
```
