"""
Backtest Report Writer

Exports all backtest artifacts:
  1. trades.csv        — full trade-by-trade log
  2. equity_curve.csv  — balance snapshot after every trade
  3. summary.json      — all BacktestMetrics as JSON
  4. report.md         — human-readable markdown performance report
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime

from backtesting.metrics import BacktestMetrics
from backtesting.simulator import BacktestResult, SimulatedTrade
from logs.logger import get_logger

logger = get_logger("backtesting.report_writer")


def write_all(result: BacktestResult, metrics: BacktestMetrics, export_dir: str) -> dict[str, str]:
    """
    Write all backtest output files to `export_dir`.

    Returns:
        dict mapping artifact name → absolute file path.
    """
    os.makedirs(export_dir, exist_ok=True)
    paths: dict[str, str] = {}

    paths["trades_csv"] = _write_trades_csv(result.completed_trades, export_dir)
    paths["equity_csv"] = _write_equity_csv(result.equity_curve, export_dir)
    paths["summary_json"] = _write_summary_json(metrics, result.config, export_dir)
    paths["report_md"] = _write_markdown_report(metrics, result.config, export_dir)

    logger.info("Backtest artifacts written to: %s", export_dir)
    for name, path in paths.items():
        logger.info("  %-15s → %s", name, path)

    return paths


def _write_trades_csv(trades: list[SimulatedTrade], export_dir: str) -> str:
    path = os.path.join(export_dir, "trades.csv")
    if not trades:
        open(path, "w").close()
        return path

    fieldnames = [
        "symbol", "direction",
        "signal_candle_ts", "entry_candle_ts", "exit_candle_ts",
        "entry_price", "exit_price", "stop_loss", "take_profit",
        "quantity", "risk_distance",
        "gross_pnl_usdt", "fee_cost_usdt", "slippage_cost_usdt",
        "net_pnl_usdt", "pnl_pct",
        "exit_reason", "holding_minutes",
        "trend_state", "regime_label", "regime_score",
        "volume_ratio", "close_buffer_atr",
        "body_to_range_ratio", "distance_from_ema_atr",
        "balance_after",
    ]

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in trades:
            writer.writerow({fn: getattr(t, fn, "") for fn in fieldnames})

    logger.debug("Wrote %d trades → %s", len(trades), path)
    return path


def _write_equity_csv(equity_curve: list[float], export_dir: str) -> str:
    path = os.path.join(export_dir, "equity_curve.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["trade_index", "balance_usdt"])
        for idx, bal in enumerate(equity_curve):
            writer.writerow([idx, bal])
    logger.debug("Equity curve (%d points) → %s", len(equity_curve), path)
    return path


def _metrics_to_dict(m: BacktestMetrics) -> dict:
    d = {}
    for k, v in m.__dict__.items():
        if k in ("equity_curve", "long_stats", "short_stats"):
            continue
        d[k] = v
    d["long_stats"] = m.long_stats.__dict__
    d["short_stats"] = m.short_stats.__dict__
    return d


def _write_summary_json(metrics: BacktestMetrics, config, export_dir: str) -> str:
    path = os.path.join(export_dir, "summary.json")
    data = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "config": {
            "symbol": config.symbol,
            "initial_balance": config.initial_balance,
            "fee_rate": config.fee_rate,
            "slippage_rate": config.slippage_rate,
            "entry_mode": config.entry_mode,
            "settings_override": config.settings_override,
        },
        "metrics": _metrics_to_dict(metrics),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    logger.debug("Summary JSON → %s", path)
    return path


def _write_markdown_report(metrics: BacktestMetrics, config, export_dir: str) -> str:
    path = os.path.join(export_dir, "report.md")
    m = metrics
    c = config

    pf_str = f"{m.profit_factor:.3f}" if m.profit_factor != float("inf") else "∞"

    lines = [
        f"# Backtest Report — {c.symbol}",
        f"",
        f"> Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        f"",
        f"## Configuration",
        f"",
        f"| Parameter | Value |",
        f"|---|---|",
        f"| Symbol | {c.symbol} |",
        f"| Initial balance | {c.initial_balance:,.2f} USDT |",
        f"| Fee rate | {c.fee_rate*100:.4f}% per leg |",
        f"| Slippage rate | {c.slippage_rate*100:.4f}% per leg |",
        f"| Entry mode | {c.entry_mode} (next candle open) |",
    ]
    if c.settings_override:
        lines.append(f"| Settings override | {c.settings_override} |")

    lines += [
        f"",
        f"## Summary",
        f"",
        f"| Metric | Value |",
        f"|---|---|",
        f"| Total trades | {m.total_trades} |",
        f"| Rejected signals | {m.rejected_signals} |",
        f"| Winning trades | {m.winning_trades} ({m.win_rate*100:.1f}%) |",
        f"| Losing trades | {m.losing_trades} ({m.loss_rate*100:.1f}%) |",
        f"| Total PnL | {m.total_pnl_usdt:+,.4f} USDT |",
        f"| Gross profit | {m.gross_profit:,.4f} USDT |",
        f"| Gross loss | {m.gross_loss:,.4f} USDT |",
        f"| Profit factor | {pf_str} |",
        f"| Payoff ratio | {m.payoff_ratio:.3f} |",
        f"| Expectancy | {m.expectancy_usdt:+.4f} USDT/trade |",
        f"| Average win | {m.avg_win_usdt:+.4f} USDT |",
        f"| Average loss | {m.avg_loss_usdt:+.4f} USDT |",
        f"| Initial balance | {m.initial_balance:,.2f} USDT |",
        f"| Ending balance | {m.ending_balance:,.2f} USDT |",
        f"| Total return | {m.total_return_pct:+.2f}% |",
        f"| Max drawdown | {m.max_drawdown_usdt:,.4f} USDT ({m.max_drawdown_pct:.2f}%) |",
        f"| Total fees paid | {m.total_fees_usdt:,.4f} USDT |",
        f"| Total slippage | {m.total_slippage_usdt:,.4f} USDT |",
        f"| Avg holding time | {m.avg_trade_duration_minutes:.1f} min |",
        f"| Max holding time | {m.max_trade_duration_minutes:.1f} min |",
        f"| Min holding time | {m.min_trade_duration_minutes:.1f} min |",
    ]

    if m.regime_distribution:
        lines += [
            f"",
            f"## Regime Distribution of Trades",
            f"",
            f"| Regime Label | Count |",
            f"|---|---|",
        ]
        for label, count in sorted(m.regime_distribution.items(), key=lambda x: -x[1]):
            lines.append(f"| {label} | {count} |")

    ls = m.long_stats
    ss = m.short_stats
    lines += [
        f"",
        f"## Direction Breakdown",
        f"",
        f"| Metric | Long | Short |",
        f"|---|---|---|",
        f"| Trades | {ls.trades} | {ss.trades} |",
        f"| Wins | {ls.wins} | {ss.wins} |",
        f"| Win rate | {ls.win_rate*100:.1f}% | {ss.win_rate*100:.1f}% |",
        f"| Total PnL | {ls.total_pnl:+.4f} | {ss.total_pnl:+.4f} |",
        f"| Avg PnL/trade | {ls.avg_pnl:+.4f} | {ss.avg_pnl:+.4f} |",
    ]

    lines += [
        f"",
        f"## Limitations and Assumptions",
        f"",
        f"- Entry at next-candle open after signal confirmation (not same candle close)",
        f"- Slippage modelled as a flat rate on entry **and** exit (both legs)",
        f"- Fees charged on both entry and exit legs",
        f"- If SL and TP are both touched in the same candle, **SL is assumed hit first** (conservative)",
        f"- 1h trend data uses only fully-closed 1h candles at each 15m signal time",
        f"- Cooldown and frequency limits are tracked in-memory and reset between runs",
        f"- Position sizing follows live-bot logic: risk {c.initial_balance:.0f} × RISK_PERCENT% / stop_distance",
        f"- Leverage is not modelled — position value is capped at account balance",
        f"- No funding rate, no borrowing cost (testnet demo conditions)",
    ]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    logger.debug("Markdown report → %s", path)
    return path
