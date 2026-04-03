"""
Convenience entry point for the backtesting engine.

Run from the crypto_bot/ directory:

    python run_backtest.py [options]

This is equivalent to:

    python -m backtesting.backtest_runner [options]

Examples:

    # Run with defaults (symbols from BACKTEST_SYMBOLS or SYMBOLS in .env)
    python run_backtest.py

    # Test BTCUSDT only, ~63 days of 15m data
    python run_backtest.py --symbols BTCUSDT --candles 6000

    # Test both symbols with a custom balance and no CSV cache
    python run_backtest.py --symbols BTCUSDT ETHUSDT --balance 5000 --no-cache

    # Run base backtest + parameter sweep for BTCUSDT
    python run_backtest.py --symbols BTCUSDT --candles 4000 --sweep

    # Write results to a custom directory
    python run_backtest.py --export-dir /tmp/bt_results

All options:
    --symbols SYMBOL [SYMBOL ...]   Symbols to test
    --candles N                     Total 15m candles to load
    --balance USDT                  Starting simulation balance
    --fee-rate RATE                 Fee per leg (decimal, e.g. 0.0004)
    --slippage-rate RATE            Slippage per leg (decimal, e.g. 0.0002)
    --export-dir DIR                Output directory
    --no-cache                      Skip CSV cache, always fetch fresh data
    --sweep                         Run parameter sweep after base backtest
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtesting.backtest_runner import main

if __name__ == "__main__":
    sys.exit(main())
