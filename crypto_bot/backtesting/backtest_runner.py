"""
Backtest Runner — Module Entrypoint

Run with:
    cd crypto_bot
    python -m backtesting.backtest_runner [options]

Or via the convenience script:
    cd crypto_bot
    python run_backtest.py [options]

This module:
  1. Parses CLI arguments
  2. Loads settings (no API key required — uses public Binance OHLCV endpoint)
  3. Fetches historical OHLCV data for each symbol
  4. Runs the walk-forward simulator with the same strategy logic as the live bot
  5. Computes performance metrics
  6. Writes all result files to the export directory
  7. Optionally runs a parameter sweep

Exit codes:
  0 — completed successfully (even if zero trades were found)
  1 — configuration or data error
"""

from __future__ import annotations

import argparse
import os
import sys

# Ensure crypto_bot root is on the path when run as a module from within
# the backtesting/ subdirectory.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config.settings import settings
from logs.logger import get_logger
from backtesting.data_loader import fetch_ohlcv_paginated
from backtesting.simulator import BacktestConfig, run_backtest
from backtesting.metrics import compute_metrics
from backtesting.report_writer import write_all

logger = get_logger("backtesting.runner")


# ─────────────────────────────────────────────────────────────────────────────
# CLI argument parser
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backtest_runner",
        description=(
            "Walk-forward backtest for the Binance Futures breakout strategy.\n"
            "No API key required — uses the public Binance USDT-M OHLCV endpoint.\n"
            "Settings can be set via .env or environment variables; CLI flags override."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--symbols",
        nargs="+",
        metavar="SYMBOL",
        help=(
            "Symbols to backtest, e.g. BTCUSDT ETHUSDT. "
            "Defaults to BACKTEST_SYMBOLS setting (or SYMBOLS if not set)."
        ),
    )
    parser.add_argument(
        "--candles",
        type=int,
        metavar="N",
        help=(
            "Total 15m candles to load per symbol. "
            "Default: BACKTEST_CANDLE_LIMIT (%(default)s). "
            "1h candles are fetched as candles // 4 + buffer. "
            "2000 ≈ 21 days | 6000 ≈ 63 days | 10000 ≈ 104 days."
        ),
    )
    parser.add_argument(
        "--balance",
        type=float,
        metavar="USDT",
        help=(
            "Starting balance for simulation. "
            "Default: BACKTEST_INITIAL_BALANCE setting."
        ),
    )
    parser.add_argument(
        "--fee-rate",
        type=float,
        metavar="RATE",
        dest="fee_rate",
        help=(
            "Fee per leg as decimal (e.g. 0.0004 = 0.04%%). "
            "Default: BACKTEST_FEE_RATE setting."
        ),
    )
    parser.add_argument(
        "--slippage-rate",
        type=float,
        metavar="RATE",
        dest="slippage_rate",
        help=(
            "Slippage per leg as decimal (e.g. 0.0002 = 0.02%%). "
            "Default: BACKTEST_SLIPPAGE_RATE setting."
        ),
    )
    parser.add_argument(
        "--export-dir",
        metavar="DIR",
        dest="export_dir",
        help=(
            "Directory for output files. "
            "Default: BACKTEST_EXPORT_DIR setting."
        ),
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        default=False,
        help="Disable CSV caching of downloaded OHLCV data.",
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        default=False,
        help=(
            "Run a parameter sweep after the base backtest. "
            "Uses the built-in EXAMPLE_SWEEP grid. "
            "Warning: can be slow (many combinations)."
        ),
    )

    return parser


# ─────────────────────────────────────────────────────────────────────────────
# Data loading helper
# ─────────────────────────────────────────────────────────────────────────────

def _load_data(
    symbol: str,
    candle_limit: int,
    export_dir: str,
    use_cache: bool,
) -> tuple:
    """
    Fetch 15m and 1h OHLCV data for a symbol.

    The 1h series needs enough candles to compute all indicators used by the
    strategy (EMA50 requires 50 bars, ATR MA 14, swing lookback 20, slope 5).
    We fetch candle_limit // 4 + 200 candles of 1h data to provide this buffer.

    Returns:
        (df_15m, df_1h) as raw DataFrames with UTC DatetimeIndex.
    """
    cache_dir = os.path.join(export_dir, "cache") if not (export_dir is None) else None

    total_1h = max(candle_limit // 4 + 200, 500)

    logger.info("Loading 15m data for %s (%d candles)...", symbol, candle_limit)
    df_15m = fetch_ohlcv_paginated(
        symbol=symbol,
        timeframe="15m",
        total_candles=candle_limit,
        cache_dir=cache_dir,
        use_cache=use_cache,
    )

    logger.info("Loading 1h data for %s (%d candles)...", symbol, total_1h)
    df_1h = fetch_ohlcv_paginated(
        symbol=symbol,
        timeframe="1h",
        total_candles=total_1h,
        cache_dir=cache_dir,
        use_cache=use_cache,
    )

    return df_15m, df_1h


# ─────────────────────────────────────────────────────────────────────────────
# Single-symbol backtest runner
# ─────────────────────────────────────────────────────────────────────────────

def run_single(
    symbol: str,
    candle_limit: int,
    initial_balance: float,
    fee_rate: float,
    slippage_rate: float,
    export_dir: str,
    use_cache: bool = True,
) -> None:
    """
    Fetch data, run backtest, compute metrics, and write all output files
    for a single symbol. Logs a summary on completion.
    """
    symbol_export = os.path.join(export_dir, symbol)
    os.makedirs(symbol_export, exist_ok=True)

    df_15m, df_1h = _load_data(symbol, candle_limit, export_dir, use_cache)

    config = BacktestConfig(
        symbol=symbol,
        initial_balance=initial_balance,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
        entry_mode=settings.BACKTEST_ENTRY_MODE,
    )

    result = run_backtest(config, df_1h, df_15m)
    metrics = compute_metrics(
        result.completed_trades,
        result.rejected_count,
        initial_balance,
        result.equity_curve,
    )

    paths = write_all(result, metrics, symbol_export)

    logger.info(
        "─── %s RESULT ─── trades=%d wins=%d(%.1f%%) "
        "PnL=%.2f USDT return=%.2f%% maxDD=%.2f%% "
        "pf=%s expectancy=%.4f",
        symbol,
        metrics.total_trades,
        metrics.winning_trades,
        metrics.win_rate * 100,
        metrics.total_pnl_usdt,
        metrics.total_return_pct,
        metrics.max_drawdown_pct,
        f"{metrics.profit_factor:.3f}" if metrics.profit_factor != float("inf") else "∞",
        metrics.expectancy_usdt,
    )
    logger.info("Output files:")
    for name, path in paths.items():
        logger.info("  %-15s → %s", name, path)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Resolve effective values: CLI flag > env/settings default
    symbols = args.symbols or settings.BACKTEST_SYMBOLS
    candle_limit = args.candles or settings.BACKTEST_CANDLE_LIMIT
    initial_balance = args.balance or settings.BACKTEST_INITIAL_BALANCE
    fee_rate = args.fee_rate if args.fee_rate is not None else settings.BACKTEST_FEE_RATE
    slippage_rate = args.slippage_rate if args.slippage_rate is not None else settings.BACKTEST_SLIPPAGE_RATE
    export_dir = args.export_dir or settings.BACKTEST_EXPORT_DIR
    use_cache = not args.no_cache

    logger.info(
        "=== BACKTEST RUN START ===\n"
        "  symbols       : %s\n"
        "  candles (15m) : %d\n"
        "  balance       : %.2f USDT\n"
        "  fee rate      : %.4f%%/leg\n"
        "  slippage rate : %.4f%%/leg\n"
        "  entry mode    : %s\n"
        "  export dir    : %s\n"
        "  cache         : %s",
        ", ".join(symbols),
        candle_limit,
        initial_balance,
        fee_rate * 100,
        slippage_rate * 100,
        settings.BACKTEST_ENTRY_MODE,
        export_dir,
        "enabled" if use_cache else "disabled",
    )

    errors: list[str] = []

    for symbol in symbols:
        try:
            run_single(
                symbol=symbol,
                candle_limit=candle_limit,
                initial_balance=initial_balance,
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
                export_dir=export_dir,
                use_cache=use_cache,
            )
        except Exception as exc:
            logger.error("FAILED for %s: %s", symbol, exc, exc_info=True)
            errors.append(f"{symbol}: {exc}")

    if args.sweep:
        logger.info("=== PARAMETER SWEEP ===")
        from backtesting.parameter_sweeper import sweep, EXAMPLE_SWEEP

        for symbol in symbols:
            try:
                logger.info("Starting sweep for %s with %d parameter combinations...",
                            symbol, _count_combos(EXAMPLE_SWEEP))
                # Use cached data if available — re-fetch if not
                df_15m, df_1h = _load_data(symbol, candle_limit, export_dir, use_cache)
                sweep_dir = os.path.join(export_dir, symbol, "sweep")
                sweep(
                    symbol=symbol,
                    df_1h=df_1h,
                    df_15m=df_15m,
                    param_grid=EXAMPLE_SWEEP,
                    initial_balance=initial_balance,
                    fee_rate=fee_rate,
                    slippage_rate=slippage_rate,
                    export_dir=sweep_dir,
                )
            except Exception as exc:
                logger.error("Sweep FAILED for %s: %s", symbol, exc, exc_info=True)
                errors.append(f"sweep/{symbol}: {exc}")

    if errors:
        logger.error("=== COMPLETED WITH ERRORS ===")
        for e in errors:
            logger.error("  %s", e)
        return 1

    logger.info("=== BACKTEST RUN COMPLETE ===")
    return 0


def _count_combos(param_grid: dict) -> int:
    import math
    return math.prod(len(v) for v in param_grid.values())


if __name__ == "__main__":
    sys.exit(main())
