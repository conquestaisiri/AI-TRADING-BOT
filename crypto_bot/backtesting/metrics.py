"""
Backtest Performance Metrics

Computes a full set of performance metrics from a list of completed
SimulatedTrade objects. Returns a typed BacktestMetrics dataclass.

Metrics computed:
  - Total / winning / losing / skipped trades
  - Win rate, loss rate
  - Average win, average loss
  - Payoff ratio (avg_win / avg_loss)
  - Expectancy (expected value per trade in USDT)
  - Profit factor (gross_profit / gross_loss)
  - Total PnL (net of fees and slippage)
  - Ending balance
  - Maximum drawdown (absolute and %)
  - Average holding duration
  - Long-only and short-only breakdowns
  - Regime distribution of trades taken
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backtesting.simulator import SimulatedTrade


@dataclass
class TradeGroupStats:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    win_rate: float = 0.0


@dataclass
class BacktestMetrics:
    # ── Counts ────────────────────────────────────────────────────────
    total_trades: int
    winning_trades: int
    losing_trades: int
    rejected_signals: int

    # ── Rate metrics ──────────────────────────────────────────────────
    win_rate: float        # 0–1
    loss_rate: float       # 0–1

    # ── PnL metrics ───────────────────────────────────────────────────
    total_pnl_usdt: float
    gross_profit: float
    gross_loss: float      # stored as negative number
    avg_win_usdt: float
    avg_loss_usdt: float   # stored as negative number
    payoff_ratio: float    # abs(avg_win / avg_loss)
    expectancy_usdt: float # expected value per trade
    profit_factor: float   # gross_profit / abs(gross_loss)

    # ── Balance ───────────────────────────────────────────────────────
    initial_balance: float
    ending_balance: float
    total_return_pct: float

    # ── Drawdown ──────────────────────────────────────────────────────
    max_drawdown_usdt: float
    max_drawdown_pct: float

    # ── Fees / slippage ───────────────────────────────────────────────
    total_fees_usdt: float
    total_slippage_usdt: float

    # ── Duration ──────────────────────────────────────────────────────
    avg_trade_duration_minutes: float
    max_trade_duration_minutes: float
    min_trade_duration_minutes: float

    # ── Direction breakdown ───────────────────────────────────────────
    long_stats: TradeGroupStats
    short_stats: TradeGroupStats

    # ── Regime distribution ───────────────────────────────────────────
    regime_distribution: dict[str, int]   # label → count of trades taken

    # ── Equity curve ──────────────────────────────────────────────────
    equity_curve: list[float]             # balance after each trade (chronological)


def _group_stats(trades: list["SimulatedTrade"]) -> TradeGroupStats:
    if not trades:
        return TradeGroupStats()
    wins = [t for t in trades if t.net_pnl_usdt > 0]
    losses = [t for t in trades if t.net_pnl_usdt <= 0]
    total_pnl = sum(t.net_pnl_usdt for t in trades)
    avg_pnl = total_pnl / len(trades)
    win_rate = len(wins) / len(trades)
    return TradeGroupStats(
        trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        total_pnl=round(total_pnl, 4),
        avg_pnl=round(avg_pnl, 4),
        win_rate=round(win_rate, 4),
    )


def compute_metrics(
    completed_trades: list["SimulatedTrade"],
    rejected_count: int,
    initial_balance: float,
    equity_curve: list[float],
) -> BacktestMetrics:
    """
    Compute all performance metrics from a list of completed SimulatedTrade objects.

    Args:
        completed_trades: trades that were opened and closed during the backtest
        rejected_count:   number of signals that were not approved by the strategy engine
        initial_balance:  starting USDT balance
        equity_curve:     list of balance values after each closed trade

    Returns:
        BacktestMetrics dataclass
    """
    if not completed_trades:
        empty = TradeGroupStats()
        return BacktestMetrics(
            total_trades=0, winning_trades=0, losing_trades=0,
            rejected_signals=rejected_count,
            win_rate=0.0, loss_rate=0.0,
            total_pnl_usdt=0.0, gross_profit=0.0, gross_loss=0.0,
            avg_win_usdt=0.0, avg_loss_usdt=0.0,
            payoff_ratio=0.0, expectancy_usdt=0.0, profit_factor=0.0,
            initial_balance=initial_balance,
            ending_balance=initial_balance if not equity_curve else equity_curve[-1],
            total_return_pct=0.0,
            max_drawdown_usdt=0.0, max_drawdown_pct=0.0,
            total_fees_usdt=0.0, total_slippage_usdt=0.0,
            avg_trade_duration_minutes=0.0,
            max_trade_duration_minutes=0.0,
            min_trade_duration_minutes=0.0,
            long_stats=empty, short_stats=empty,
            regime_distribution={},
            equity_curve=equity_curve,
        )

    wins = [t for t in completed_trades if t.net_pnl_usdt > 0]
    losses = [t for t in completed_trades if t.net_pnl_usdt <= 0]
    total_trades = len(completed_trades)

    gross_profit = sum(t.net_pnl_usdt for t in wins) if wins else 0.0
    gross_loss = sum(t.net_pnl_usdt for t in losses) if losses else 0.0
    total_pnl = sum(t.net_pnl_usdt for t in completed_trades)

    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0

    payoff_ratio = (avg_win / abs(avg_loss)) if avg_loss != 0 else 0.0
    expectancy = total_pnl / total_trades
    profit_factor = (gross_profit / abs(gross_loss)) if gross_loss != 0 else float("inf")

    ending_balance = equity_curve[-1] if equity_curve else initial_balance
    total_return_pct = ((ending_balance - initial_balance) / initial_balance) * 100

    # Maximum drawdown
    peak = initial_balance
    max_dd_abs = 0.0
    running = initial_balance
    for pnl in [t.net_pnl_usdt for t in completed_trades]:
        running += pnl
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd_abs:
            max_dd_abs = dd
    max_dd_pct = (max_dd_abs / peak * 100) if peak > 0 else 0.0

    # Duration
    durations = [t.holding_minutes for t in completed_trades if t.holding_minutes is not None]
    avg_dur = sum(durations) / len(durations) if durations else 0.0
    max_dur = max(durations) if durations else 0.0
    min_dur = min(durations) if durations else 0.0

    # Fees / slippage
    total_fees = sum(t.fee_cost_usdt for t in completed_trades)
    total_slip = sum(t.slippage_cost_usdt for t in completed_trades)

    # Direction breakdown
    longs = [t for t in completed_trades if t.direction == "long"]
    shorts = [t for t in completed_trades if t.direction == "short"]

    # Regime distribution
    regime_dist: dict[str, int] = {}
    for t in completed_trades:
        label = t.regime_label or "unknown"
        regime_dist[label] = regime_dist.get(label, 0) + 1

    return BacktestMetrics(
        total_trades=total_trades,
        winning_trades=len(wins),
        losing_trades=len(losses),
        rejected_signals=rejected_count,
        win_rate=round(len(wins) / total_trades, 4),
        loss_rate=round(len(losses) / total_trades, 4),
        total_pnl_usdt=round(total_pnl, 4),
        gross_profit=round(gross_profit, 4),
        gross_loss=round(gross_loss, 4),
        avg_win_usdt=round(avg_win, 4),
        avg_loss_usdt=round(avg_loss, 4),
        payoff_ratio=round(payoff_ratio, 4),
        expectancy_usdt=round(expectancy, 4),
        profit_factor=round(profit_factor, 4),
        initial_balance=round(initial_balance, 4),
        ending_balance=round(ending_balance, 4),
        total_return_pct=round(total_return_pct, 4),
        max_drawdown_usdt=round(max_dd_abs, 4),
        max_drawdown_pct=round(max_dd_pct, 4),
        total_fees_usdt=round(total_fees, 4),
        total_slippage_usdt=round(total_slip, 4),
        avg_trade_duration_minutes=round(avg_dur, 2),
        max_trade_duration_minutes=round(max_dur, 2),
        min_trade_duration_minutes=round(min_dur, 2),
        long_stats=_group_stats(longs),
        short_stats=_group_stats(shorts),
        regime_distribution=regime_dist,
        equity_curve=equity_curve,
    )
