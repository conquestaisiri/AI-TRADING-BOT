# Workspace

## Overview

pnpm workspace monorepo using TypeScript. Each package manages its own dependencies.
Also contains a standalone Python crypto demo trading bot in `crypto_bot/`.

## Stack

- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24
- **Package manager**: pnpm
- **TypeScript version**: 5.9
- **API framework**: Express 5
- **Database**: PostgreSQL + Drizzle ORM
- **Validation**: Zod (`zod/v4`), `drizzle-zod`
- **API codegen**: Orval (from OpenAPI spec)
- **Build**: esbuild (CJS bundle)
- **Python**: 3.11 (for crypto_bot)

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
