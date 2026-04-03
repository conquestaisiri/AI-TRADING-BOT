"""
Parameter Sweeper

Runs multiple backtests with different strategy parameter combinations and
compares summary metrics side by side.

Usage:
    from backtesting.parameter_sweeper import sweep, EXAMPLE_SWEEP

    results = sweep(
        symbol="BTCUSDT",
        df_1h=df_1h,
        df_15m=df_15m,
        param_grid={
            "ATR_STOP_MULTIPLIER": [1.0, 1.5, 2.0],
            "REWARD_TO_RISK": [1.5, 2.0, 3.0],
        },
        initial_balance=10_000.0,
        export_dir="backtest_results/sweep",
    )

The param_grid defines which settings to vary. Each combination is tested
independently. Results are exported to `<export_dir>/sweep_results.csv`.
"""

from __future__ import annotations

import csv
import itertools
import os
from dataclasses import asdict

from backtesting.simulator import BacktestConfig, BacktestResult, run_backtest
from backtesting.metrics import compute_metrics, BacktestMetrics
from logs.logger import get_logger

logger = get_logger("backtesting.parameter_sweeper")

import pandas as pd


# A reasonable default sweep grid for exploration
EXAMPLE_SWEEP: dict[str, list] = {
    "ATR_STOP_MULTIPLIER": [1.0, 1.5, 2.0],
    "REWARD_TO_RISK": [1.5, 2.0, 2.5],
    "VOLUME_RATIO_THRESHOLD": [1.2, 1.5, 2.0],
    "BREAKOUT_CLOSE_BUFFER_RATIO": [0.05, 0.10, 0.20],
    "MAX_BODY_ATR_RATIO": [1.5, 2.0, 3.0],
}


def _grid_combos(param_grid: dict[str, list]) -> list[dict]:
    """Return all combinations of the param grid as a list of override dicts."""
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combos = list(itertools.product(*values))
    return [dict(zip(keys, combo)) for combo in combos]


def sweep(
    symbol: str,
    df_1h: pd.DataFrame,
    df_15m: pd.DataFrame,
    param_grid: dict[str, list],
    initial_balance: float = 10_000.0,
    fee_rate: float = 0.0004,
    slippage_rate: float = 0.0002,
    export_dir: str = "backtest_results/sweep",
) -> list[dict]:
    """
    Run one backtest per parameter combination in param_grid.

    Args:
        symbol:         Trading pair (e.g. "BTCUSDT")
        df_1h:          Pre-fetched 1h OHLCV DataFrame (raw, not yet enriched)
        df_15m:         Pre-fetched 15m OHLCV DataFrame (raw, not yet enriched)
        param_grid:     Dict of setting name → list of values to test
        initial_balance: Starting balance for each run
        fee_rate:       Fee rate per leg
        slippage_rate:  Slippage rate per leg
        export_dir:     Directory for sweep_results.csv

    Returns:
        List of dicts, one per combination, with all params + key metrics.
    """
    combos = _grid_combos(param_grid)
    total = len(combos)
    logger.info(
        "Parameter sweep: %d combinations across %d parameters for %s",
        total, len(param_grid), symbol,
    )

    rows: list[dict] = []

    for idx, override in enumerate(combos, 1):
        logger.info(
            "[%d/%d] Running: %s",
            idx, total,
            " | ".join(f"{k}={v}" for k, v in override.items()),
        )

        config = BacktestConfig(
            symbol=symbol,
            initial_balance=initial_balance,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
            settings_override=override,
        )

        try:
            result: BacktestResult = run_backtest(config, df_1h.copy(), df_15m.copy())
            metrics: BacktestMetrics = compute_metrics(
                result.completed_trades,
                result.rejected_count,
                initial_balance,
                result.equity_curve,
            )
        except Exception as exc:
            logger.error("[%d/%d] FAILED with %s: %s", idx, total, override, exc)
            row = {**override, "error": str(exc)}
            rows.append(row)
            continue

        row = {**override}
        row["total_trades"] = metrics.total_trades
        row["win_rate"] = metrics.win_rate
        row["profit_factor"] = metrics.profit_factor
        row["payoff_ratio"] = metrics.payoff_ratio
        row["expectancy_usdt"] = metrics.expectancy_usdt
        row["total_pnl_usdt"] = metrics.total_pnl_usdt
        row["total_return_pct"] = metrics.total_return_pct
        row["max_drawdown_pct"] = metrics.max_drawdown_pct
        row["ending_balance"] = metrics.ending_balance
        row["avg_holding_minutes"] = metrics.avg_trade_duration_minutes
        row["rejected_signals"] = metrics.rejected_signals
        rows.append(row)

        logger.info(
            "  → trades=%d win=%.1f%% pf=%.3f expectancy=%.4f pnl=%.2f dd=%.2f%%",
            metrics.total_trades, metrics.win_rate * 100,
            metrics.profit_factor if metrics.profit_factor != float("inf") else 999,
            metrics.expectancy_usdt, metrics.total_pnl_usdt, metrics.max_drawdown_pct,
        )

    # Export to CSV
    os.makedirs(export_dir, exist_ok=True)
    csv_path = os.path.join(export_dir, "sweep_results.csv")
    if rows:
        fieldnames = list(rows[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        logger.info("Sweep results (%d rows) → %s", len(rows), csv_path)
    else:
        logger.warning("No sweep results to write.")

    return rows
