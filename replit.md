# NeuralTrader — AI-Powered Crypto Trading Terminal

## Overview

Full-stack cryptocurrency trading system with a real-time AI trading terminal UI, live market data, and a 7-stage Python trading bot. Built as a pnpm workspace monorepo.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Frontend (mockup-sandbox) /__mockup                        │
│  React + Vite + Recharts + Tailwind                         │
│  • Live candlestick chart (OKX data, 15m)                   │
│  • Real-time orderbook                                      │
│  • Agent activity log (SSE stream from bot stdout)          │
│  • Multi-pair ticker strip                                   │
│  • Positions/history from SQLite + CSV                      │
└────────────────────┬────────────────────────────────────────┘
                     │ REST + SSE (/api/...)
┌────────────────────▼────────────────────────────────────────┐
│  API Server (artifacts/api-server) /api                     │
│  Node.js + Express 5 + TypeScript                           │
│  Routes:                                                    │
│  • GET  /api/bot/status         — bot state                 │
│  • POST /api/bot/start          — spawn Python bot          │
│  • POST /api/bot/stop           — kill Python bot           │
│  • GET  /api/bot/market/:pair   — live OHLCV (OKX)          │
│  • GET  /api/bot/tickers        — all pair prices (OKX)     │
│  • GET  /api/bot/orderbook/:pair— real orderbook (OKX)      │
│  • GET  /api/bot/trades         — SQLite + CSV trades       │
│  • GET  /api/activity/stream    — SSE (bot stdout)          │
└────────────────────┬────────────────────────────────────────┘
                     │ child_process.spawn
┌────────────────────▼────────────────────────────────────────┐
│  Python Bot (crypto_bot/)                                   │
│  Binance Futures Testnet                                    │
│  7-Stage signal pipeline:                                   │
│  S1 Market Analyst → S2 Data Fetcher → S3 Indicator Engine  │
│  S4 Regime Classifier → S5 Signal Generator                 │
│  S6 Risk Manager → S7 Order Executor                        │
└─────────────────────────────────────────────────────────────┘
```

## Stack

- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24
- **Package manager**: pnpm
- **TypeScript version**: 5.9
- **API framework**: Express 5
- **Market data**: OKX public REST API (fallback: KuCoin)
- **Real-time**: Server-Sent Events (SSE) for bot log streaming
- **Trade storage**: Node 24 built-in `node:sqlite` reads bot's SQLite DB
- **Build**: esbuild (CJS bundle)
- **Python**: 3.11 (for crypto_bot)
- **Charts**: Recharts (candlestick, RSI, equity curve)

## Crypto Bot (crypto_bot/)

Autonomous demo trading bot on Binance **Futures Testnet** (supports long AND short).

### Key design decisions
- Uses Futures testnet, not Spot — Spot cannot short properly
- Swing high/low uses `.shift(1)` before rolling to exclude current candle (no self-reference)
- Settings validated at startup via `settings.validate()`, not at import time
- Strategy has: EMA spread filter, ATR min-pct filter, RSI extreme filter, loss cooldown
- `build_feature_summary()` provides structured AI-ready feature dict (future AI layer hook)
- All decisions logged with full context (candle timestamps, rejection reasons, swing levels)

### Run the bot
```bash
cd crypto_bot
cp .env.example .env  # fill in Futures testnet credentials from testnet.binancefuture.com
python3 app.py
```

### Storage
- `storage/trades.db` — SQLite open trades
- `storage/closed_trades.csv` — closed trades audit log
- `logs/bot.log` — rotating log file
