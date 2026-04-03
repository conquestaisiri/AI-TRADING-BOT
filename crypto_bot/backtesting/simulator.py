"""
Walk-Forward Backtest Simulator

Iterates over historical 15m candles one at a time and applies the same
7-stage strategy logic used by the live bot. No future data leaks.

Assumptions (all configurable):
  - Entry mode: "next_open" — enter at the open of the candle after signal confirmation
  - Slippage: applied at entry and exit (adverse direction)
  - Fees: applied on both legs (entry + exit)
  - SL/TP levels: recomputed from actual entry price using same risk distance as signal
  - Same-candle SL+TP conflict: SL wins (conservative rule)
  - 1h data alignment: only 1h candles fully closed before current 15m candle are used
  - Cooldown and frequency limits: tracked in-memory via BacktestTradeStore
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Literal

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings
from features.indicators import calculate_indicators
from strategy.signal import evaluate_signal, SignalEvaluation
from storage.trade_store import Trade
from logs.logger import get_logger

logger = get_logger("backtesting.simulator")


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BacktestConfig:
    symbol: str
    initial_balance: float = 10_000.0
    fee_rate: float = 0.0004         # 0.04% per leg (Binance USDT-M taker)
    slippage_rate: float = 0.0002    # 0.02% per leg
    entry_mode: str = "next_open"    # only "next_open" supported; document clearly
    # Strategy parameter overrides (applied before running, restored after)
    settings_override: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Simulated trade record
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SimulatedTrade:
    symbol: str
    direction: str                   # "long" | "short"
    signal_candle_ts: str            # candle that generated the signal
    entry_candle_ts: str             # candle at which entry was placed
    exit_candle_ts: str              # candle at which exit was triggered
    entry_price: float               # actual fill price (with slippage)
    exit_price: float                # actual exit price (SL or TP level)
    stop_loss: float                 # SL relative to actual entry
    take_profit: float               # TP relative to actual entry
    quantity: float
    risk_distance: float
    gross_pnl_usdt: float            # PnL before fees and slippage
    fee_cost_usdt: float
    slippage_cost_usdt: float
    net_pnl_usdt: float              # gross - fees - slippage
    pnl_pct: float                   # net PnL as % of initial position value
    exit_reason: str                 # "sl" | "tp" | "end_of_data"
    holding_minutes: float           # candles held × 15
    trend_state: str
    regime_label: str
    regime_score: float
    volume_ratio: float | None
    close_buffer_atr: float | None
    body_to_range_ratio: float | None
    distance_from_ema_atr: float | None
    balance_after: float             # running balance after this trade closes


# ─────────────────────────────────────────────────────────────────────────────
# In-memory trade store (same interface as live TradeStore)
# ─────────────────────────────────────────────────────────────────────────────

class BacktestTradeStore:
    """
    In-memory implementation of the TradeStore interface.
    Allows the 7-stage evaluate_signal to apply cooldown and frequency
    checks during backtesting without touching the SQLite database.
    """

    def __init__(self):
        self._open_by_symbol: dict[str, Trade] = {}
        self._closed: list[Trade] = []

    def has_open_trade_for_symbol(self, symbol: str) -> bool:
        return symbol in self._open_by_symbol

    def save_open_trade(self, trade: Trade) -> None:
        self._open_by_symbol[trade.symbol] = trade

    def close_trade(self, trade: Trade) -> None:
        self._open_by_symbol.pop(trade.symbol, None)
        self._closed.append(trade)

    def load_open_trades(self) -> list[Trade]:
        return list(self._open_by_symbol.values())

    def get_last_closed_trade(self, symbol: str) -> Trade | None:
        candidates = [t for t in self._closed if t.symbol == symbol]
        if not candidates:
            return None
        return max(candidates, key=lambda t: t.closed_at)

    def get_recent_entry_times(self, symbol: str, since: datetime) -> list[datetime]:
        result: list[datetime] = []
        since_str = since.isoformat()
        all_trades = list(self._open_by_symbol.values()) + self._closed
        for t in all_trades:
            if t.symbol == symbol and t.opened_at >= since_str:
                try:
                    result.append(datetime.fromisoformat(t.opened_at))
                except ValueError:
                    pass
        return sorted(result)

    def get_recent_closed_trades(self, symbol: str, since: datetime) -> list[Trade]:
        since_str = since.isoformat()
        return [
            t for t in self._closed
            if t.symbol == symbol and t.closed_at >= since_str
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Result container
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    config: BacktestConfig
    completed_trades: list[SimulatedTrade]
    rejected_count: int
    equity_curve: list[float]     # balance after each closed trade
    final_balance: float
    df_15m: pd.DataFrame          # enriched entry-timeframe data (for reference)
    df_1h: pd.DataFrame           # enriched trend-timeframe data


# ─────────────────────────────────────────────────────────────────────────────
# Settings override context
# ─────────────────────────────────────────────────────────────────────────────

class _SettingsOverride:
    """Temporarily patch global settings values, restore on exit."""

    def __init__(self, overrides: dict):
        self._overrides = overrides
        self._saved: dict = {}

    def __enter__(self):
        for k, v in self._overrides.items():
            if hasattr(settings, k):
                self._saved[k] = getattr(settings, k)
                setattr(settings, k, v)
            else:
                logger.warning("BacktestConfig.settings_override: unknown key '%s'", k)

    def __exit__(self, *_):
        for k, v in self._saved.items():
            setattr(settings, k, v)


# ─────────────────────────────────────────────────────────────────────────────
# Core simulation helpers
# ─────────────────────────────────────────────────────────────────────────────

def _align_1h_slice(df_1h: pd.DataFrame, candle_15m_time: pd.Timestamp) -> pd.DataFrame:
    """
    Return only 1h candles that were fully closed before `candle_15m_time`.

    A 1h candle at index T represents the period [T, T+1h). Its close time is
    T + 1h. We include it only if T + 1h <= candle_15m_time, i.e. T < candle_15m_time.
    Using strict less-than prevents future leakage.
    """
    cutoff = candle_15m_time - pd.Timedelta(hours=1)
    return df_1h[df_1h.index <= cutoff]


def _simulate_exit(
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    df_15m_slice: pd.DataFrame,
    entry_idx: int,
) -> tuple[float, int, str]:
    """
    Scan candles from entry_idx+1 onwards for SL or TP hits.

    Returns:
        (exit_price, exit_candle_idx, exit_reason)
        exit_reason: "sl" | "tp" | "end_of_data"

    Conservative same-candle rule: if both SL and TP are touched in the
    same candle, SL is assumed to have been hit first.
    """
    for j in range(entry_idx + 1, len(df_15m_slice)):
        row = df_15m_slice.iloc[j]
        high = float(row["high"])
        low = float(row["low"])

        if direction == "long":
            sl_hit = low <= stop_loss
            tp_hit = high >= take_profit
        else:
            sl_hit = high >= stop_loss
            tp_hit = low <= take_profit

        if sl_hit and tp_hit:
            # Conservative: SL wins — we cannot know intrabar order
            return stop_loss, j, "sl"
        if sl_hit:
            return stop_loss, j, "sl"
        if tp_hit:
            return take_profit, j, "tp"

    # Position still open at end of data
    last = df_15m_slice.iloc[-1]
    return float(last["close"]), len(df_15m_slice) - 1, "end_of_data"


def _compute_trade_costs(
    direction: str,
    entry_price: float,
    exit_price: float,
    quantity: float,
    fee_rate: float,
    slippage_rate: float,
) -> tuple[float, float, float, float]:
    """
    Compute gross PnL, fees, slippage, and net PnL.

    Slippage is modelled as a fixed-rate cost on both legs (adverse to PnL).
    Fees are charged on the position value for both entry and exit legs.

    Returns:
        (gross_pnl, fee_cost, slippage_cost, net_pnl)
    """
    position_value = entry_price * quantity

    if direction == "long":
        gross_pnl = (exit_price - entry_price) * quantity
    else:
        gross_pnl = (entry_price - exit_price) * quantity

    fee_cost = fee_rate * position_value * 2          # entry + exit legs
    slippage_cost = slippage_rate * position_value * 2  # entry + exit legs

    net_pnl = gross_pnl - fee_cost - slippage_cost
    return gross_pnl, fee_cost, slippage_cost, net_pnl


# ─────────────────────────────────────────────────────────────────────────────
# Main simulation function
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest(
    config: BacktestConfig,
    df_1h_raw: pd.DataFrame,
    df_15m_raw: pd.DataFrame,
) -> BacktestResult:
    """
    Run a walk-forward backtest for one symbol using pre-fetched OHLCV DataFrames.

    Args:
        config:     BacktestConfig with all simulation parameters
        df_1h_raw:  Raw 1h OHLCV DataFrame with UTC DatetimeIndex
        df_15m_raw: Raw 15m OHLCV DataFrame with UTC DatetimeIndex

    Returns:
        BacktestResult with all completed trades, metrics input, and equity curve.

    No future data is used: at each 15m candle, only past 1h data is made available.
    """
    with _SettingsOverride(config.settings_override):
        return _run(config, df_1h_raw, df_15m_raw)


def _run(
    config: BacktestConfig,
    df_1h_raw: pd.DataFrame,
    df_15m_raw: pd.DataFrame,
) -> BacktestResult:
    symbol = config.symbol
    logger.info(
        "=== BACKTEST START: %s | balance=%.2f | fee=%.4f%% slippage=%.4f%% ===",
        symbol, config.initial_balance,
        config.fee_rate * 100, config.slippage_rate * 100,
    )

    # ── Compute indicators on full dataset ──────────────────────────────────
    # All indicator computation happens once on the full dataset.
    # When we slice to a lookback window per candle, no future data flows back
    # because calculate_indicators is deterministic given a fixed input slice.
    df_1h = calculate_indicators(
        df_1h_raw,
        ema_fast=settings.EMA_FAST,
        ema_slow=settings.EMA_SLOW,
        rsi_period=settings.RSI_PERIOD,
        atr_period=settings.ATR_PERIOD,
        atr_ma_period=settings.ATR_MA_PERIOD,
        ema_slope_period=settings.EMA_SLOPE_PERIOD,
        volume_avg_period=settings.VOLUME_AVG_PERIOD,
        swing_lookback=settings.SWING_LOOKBACK,
    )
    df_15m = calculate_indicators(
        df_15m_raw,
        ema_fast=settings.EMA_FAST,
        ema_slow=settings.EMA_SLOW,
        rsi_period=settings.RSI_PERIOD,
        atr_period=settings.ATR_PERIOD,
        atr_ma_period=settings.ATR_MA_PERIOD,
        ema_slope_period=settings.EMA_SLOPE_PERIOD,
        volume_avg_period=settings.VOLUME_AVG_PERIOD,
        swing_lookback=settings.SWING_LOOKBACK,
    )

    # Minimum index we can start from (enough history for indicators)
    min_start = max(settings.SWING_LOOKBACK + settings.ATR_PERIOD + 20, 60)

    # ── Walk-forward state ──────────────────────────────────────────────────
    balance = config.initial_balance
    store = BacktestTradeStore()
    completed_trades: list[SimulatedTrade] = []
    equity_curve: list[float] = [balance]
    rejected_count = 0

    # Pending entry: approved signal waiting for next candle's open
    pending_signal: SignalEvaluation | None = None
    pending_signal_idx: int | None = None

    # Open position state
    open_trade_start_idx: int | None = None
    open_trade_entry: float | None = None
    open_trade_sl: float | None = None
    open_trade_tp: float | None = None
    open_trade_qty: float | None = None
    open_trade_signal: SignalEvaluation | None = None
    open_trade_entry_ts: str | None = None

    n = len(df_15m)
    logger.info("Walking forward over %d × 15m candles (starting at index %d)...", n, min_start)

    for i in range(min_start, n):
        candle = df_15m.iloc[i]
        candle_time: pd.Timestamp = candle.name
        open_price = float(candle["open"])
        high_price = float(candle["high"])
        low_price = float(candle["low"])
        close_price = float(candle["close"])
        candle_ts_str = candle_time.isoformat()

        # ── 1. Execute pending entry (entered this candle's open) ──────────
        if pending_signal is not None and pending_signal_idx == i - 1:
            direction = pending_signal.direction
            risk_distance = pending_signal.risk_distance

            # Entry price = next candle open with slippage (adverse)
            if direction == "long":
                actual_entry = open_price * (1 + config.slippage_rate)
                actual_sl = actual_entry - risk_distance
                actual_tp = actual_entry + risk_distance * settings.REWARD_TO_RISK
            else:
                actual_entry = open_price * (1 - config.slippage_rate)
                actual_sl = actual_entry + risk_distance
                actual_tp = actual_entry - risk_distance * settings.REWARD_TO_RISK

            # Position sizing: risk RISK_PERCENT% of current balance
            risk_amount = balance * (settings.RISK_PERCENT / 100.0)
            qty = risk_amount / risk_distance if risk_distance > 0 else 0
            position_value = qty * actual_entry
            if position_value > balance:
                qty = balance / actual_entry

            if qty > 0:
                # Register in BacktestTradeStore for cooldown/frequency tracking
                trade_record = Trade(
                    id=f"{symbol}_{candle_ts_str}",
                    symbol=symbol,
                    direction=direction,
                    entry_price=actual_entry,
                    stop_loss=actual_sl,
                    take_profit=actual_tp,
                    quantity=qty,
                    risk_amount_usdt=risk_amount,
                    reward_amount_usdt=risk_amount * settings.REWARD_TO_RISK,
                    risk_distance=risk_distance,
                    atr=pending_signal.atr or 0.0,
                    candle_timestamp=pending_signal.candle_timestamp,
                    trend_1h=pending_signal.trend_state,
                    regime_label=pending_signal.regime_label,
                    regime_score=pending_signal.regime_score or 0.0,
                    opened_at=datetime.now(timezone.utc).isoformat(),
                )
                store.save_open_trade(trade_record)

                open_trade_start_idx = i
                open_trade_entry = actual_entry
                open_trade_sl = actual_sl
                open_trade_tp = actual_tp
                open_trade_qty = qty
                open_trade_signal = pending_signal
                open_trade_entry_ts = candle_ts_str

                logger.debug(
                    "%s ENTRY [%s] @ %.4f | SL=%.4f TP=%.4f qty=%.6f | regime=%s(%.2f)",
                    candle_ts_str, direction.upper(), actual_entry,
                    actual_sl, actual_tp, qty,
                    pending_signal.regime_label, pending_signal.regime_score or 0,
                )
            else:
                logger.warning("%s: qty=0 — skipping entry.", candle_ts_str)

            pending_signal = None
            pending_signal_idx = None

        # ── 2. Check open position for SL/TP ─────────────────────────────
        if open_trade_start_idx is not None:
            direction = open_trade_signal.direction
            sl = open_trade_sl
            tp = open_trade_tp
            entry = open_trade_entry
            qty = open_trade_qty

            sl_hit = tp_hit = False
            if direction == "long":
                sl_hit = low_price <= sl
                tp_hit = high_price >= tp
            else:
                sl_hit = high_price >= sl
                tp_hit = low_price <= tp

            exit_reason: str | None = None
            exit_price: float | None = None

            if sl_hit and tp_hit:
                # Conservative: SL wins
                exit_reason = "sl"
                exit_price = sl
            elif sl_hit:
                exit_reason = "sl"
                exit_price = sl
            elif tp_hit:
                exit_reason = "tp"
                exit_price = tp

            if exit_reason is not None:
                gross_pnl, fee_cost, slippage_cost, net_pnl = _compute_trade_costs(
                    direction, entry, exit_price, qty,
                    config.fee_rate, config.slippage_rate,
                )
                balance += net_pnl
                equity_curve.append(round(balance, 4))

                holding_candles = i - open_trade_start_idx
                holding_minutes = holding_candles * 15.0

                sim_trade = SimulatedTrade(
                    symbol=symbol,
                    direction=direction,
                    signal_candle_ts=open_trade_signal.candle_timestamp,
                    entry_candle_ts=open_trade_entry_ts,
                    exit_candle_ts=candle_ts_str,
                    entry_price=round(entry, 6),
                    exit_price=round(exit_price, 6),
                    stop_loss=round(sl, 6),
                    take_profit=round(tp, 6),
                    quantity=round(qty, 8),
                    risk_distance=round(open_trade_signal.risk_distance, 6),
                    gross_pnl_usdt=round(gross_pnl, 4),
                    fee_cost_usdt=round(fee_cost, 4),
                    slippage_cost_usdt=round(slippage_cost, 4),
                    net_pnl_usdt=round(net_pnl, 4),
                    pnl_pct=round((net_pnl / (entry * qty)) * 100, 4) if entry * qty > 0 else 0.0,
                    exit_reason=exit_reason,
                    holding_minutes=holding_minutes,
                    trend_state=open_trade_signal.trend_state,
                    regime_label=open_trade_signal.regime_label,
                    regime_score=open_trade_signal.regime_score or 0.0,
                    volume_ratio=open_trade_signal.volume_ratio,
                    close_buffer_atr=open_trade_signal.close_buffer_atr,
                    body_to_range_ratio=open_trade_signal.body_to_range_ratio,
                    distance_from_ema_atr=open_trade_signal.distance_from_ema_atr,
                    balance_after=round(balance, 4),
                )
                completed_trades.append(sim_trade)

                # Update BacktestTradeStore for cooldown tracking
                open_stored = store._open_by_symbol.get(symbol)
                if open_stored:
                    open_stored.status = "closed_sl" if exit_reason == "sl" else "closed_tp"
                    open_stored.closed_at = datetime.now(timezone.utc).isoformat()
                    open_stored.close_price = exit_price
                    open_stored.pnl_usdt = net_pnl
                    store.close_trade(open_stored)

                logger.debug(
                    "%s EXIT [%s] %s @ %.4f | gross=%.4f fee=%.4f slip=%.4f net=%.4f | "
                    "held=%dmin balance=%.2f",
                    candle_ts_str, exit_reason.upper(), direction.upper(),
                    exit_price, gross_pnl, fee_cost, slippage_cost, net_pnl,
                    int(holding_minutes), balance,
                )

                # Reset open trade state
                open_trade_start_idx = None
                open_trade_entry = None
                open_trade_sl = None
                open_trade_tp = None
                open_trade_qty = None
                open_trade_signal = None
                open_trade_entry_ts = None

        # ── 3. Look for new signal (only if no open position, no pending entry) ──
        if open_trade_start_idx is not None or pending_signal is not None:
            continue  # already busy

        # Align 1h data to avoid future leakage
        df_1h_slice = _align_1h_slice(df_1h, candle_time)
        if len(df_1h_slice) < settings.SWING_LOOKBACK + settings.ATR_PERIOD + 5:
            continue  # not enough 1h data yet

        # Slice 15m data up to and including current candle (no future data)
        df_15m_slice = df_15m.iloc[:i + 1]

        signal = evaluate_signal(
            symbol, df_1h_slice, df_15m_slice, store, balance
        )

        if signal.approved:
            pending_signal = signal
            pending_signal_idx = i
            logger.info(
                "SIGNAL APPROVED: %s [%s] @ candle %s → entering at next candle open",
                symbol, signal.direction, signal.candle_timestamp,
            )
        else:
            rejected_count += 1

    # ── Handle any position still open at end of data ─────────────────────
    if open_trade_start_idx is not None and open_trade_signal is not None:
        last_candle = df_15m.iloc[-1]
        exit_price = float(last_candle["close"])
        direction = open_trade_signal.direction
        entry = open_trade_entry
        qty = open_trade_qty

        gross_pnl, fee_cost, slippage_cost, net_pnl = _compute_trade_costs(
            direction, entry, exit_price, qty,
            config.fee_rate, config.slippage_rate,
        )
        balance += net_pnl
        equity_curve.append(round(balance, 4))
        holding_minutes = (len(df_15m) - 1 - open_trade_start_idx) * 15.0

        sim_trade = SimulatedTrade(
            symbol=symbol,
            direction=direction,
            signal_candle_ts=open_trade_signal.candle_timestamp,
            entry_candle_ts=open_trade_entry_ts,
            exit_candle_ts=str(last_candle.name),
            entry_price=round(entry, 6),
            exit_price=round(exit_price, 6),
            stop_loss=round(open_trade_sl, 6),
            take_profit=round(open_trade_tp, 6),
            quantity=round(qty, 8),
            risk_distance=round(open_trade_signal.risk_distance, 6),
            gross_pnl_usdt=round(gross_pnl, 4),
            fee_cost_usdt=round(fee_cost, 4),
            slippage_cost_usdt=round(slippage_cost, 4),
            net_pnl_usdt=round(net_pnl, 4),
            pnl_pct=round((net_pnl / (entry * qty)) * 100, 4) if entry * qty > 0 else 0.0,
            exit_reason="end_of_data",
            holding_minutes=holding_minutes,
            trend_state=open_trade_signal.trend_state,
            regime_label=open_trade_signal.regime_label,
            regime_score=open_trade_signal.regime_score or 0.0,
            volume_ratio=open_trade_signal.volume_ratio,
            close_buffer_atr=open_trade_signal.close_buffer_atr,
            body_to_range_ratio=open_trade_signal.body_to_range_ratio,
            distance_from_ema_atr=open_trade_signal.distance_from_ema_atr,
            balance_after=round(balance, 4),
        )
        completed_trades.append(sim_trade)
        logger.info("Open position closed at end-of-data @ %.4f net_pnl=%.4f", exit_price, net_pnl)

    logger.info(
        "=== BACKTEST COMPLETE: %s | trades=%d rejected=%d "
        "start=%.2f end=%.2f ===",
        symbol, len(completed_trades), rejected_count,
        config.initial_balance, balance,
    )

    return BacktestResult(
        config=config,
        completed_trades=completed_trades,
        rejected_count=rejected_count,
        equity_curve=equity_curve,
        final_balance=round(balance, 4),
        df_15m=df_15m,
        df_1h=df_1h,
    )
