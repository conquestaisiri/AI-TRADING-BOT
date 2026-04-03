"""
7-Stage Signal Evaluation Engine

Each call to evaluate_signal() runs through:

  Stage 1: Data sufficiency validation
  Stage 2: Higher timeframe trend determination (1h EMA alignment)
  Stage 3: Market regime classification (rule-based, not AI)
  Stage 4: Breakout candidate detection (price vs shifted swing level)
  Stage 5: Breakout quality validation (body, close buffer, wick rejection)
  Stage 6: Overextension, cooldown, and trade frequency checks
  Stage 7: Final approval and signal construction

Returns a rich SignalEvaluation object for every symbol — approved or rejected.
Rejected signals include the exact rejection reason code and detail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Literal
import pandas as pd

from config.settings import settings
from strategy.regime import classify_regime, RegimeResult
from logs.logger import get_logger

if TYPE_CHECKING:
    from storage.trade_store import TradeStore

logger = get_logger("strategy.signal")

Direction = Literal["long", "short"]


# ─────────────────────────────────────────────────────────────────────────────
# Signal result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SignalEvaluation:
    """
    Full evaluation record for one symbol per cycle.
    Contains every metric used in each decision stage.
    Approved signals have all fields populated; rejected signals have only
    fields up to the stage where rejection occurred.
    """
    symbol: str
    evaluated_at: str           # ISO UTC timestamp of evaluation
    candle_timestamp: str       # Timestamp of the triggering 15m candle

    # ── Decision ────────────────────────────────────────────────────────
    approved: bool
    rejection_code: str | None
    rejection_reason: str | None
    summary: str                # One-line human-readable outcome

    # ── Direction ───────────────────────────────────────────────────────
    direction: str | None       # "long" | "short" | None

    # ── Trend (Stage 2) ─────────────────────────────────────────────────
    trend_state: str            # "bullish" | "bearish" | "neutral"
    ema_spread_pct: float | None

    # ── Regime (Stage 3) ────────────────────────────────────────────────
    regime_label: str           # "trending" | "ranging" | "choppy"
    regime_score: float | None
    regime_atr_expanding: bool | None

    # ── Breakout (Stage 4) ──────────────────────────────────────────────
    breakout_level: float | None   # swing_high (long) or swing_low (short)
    close_vs_level: float | None   # how far close is beyond breakout_level
    close_buffer_atr: float | None # close_vs_level / ATR

    # ── Breakout quality (Stage 5) ──────────────────────────────────────
    volume_ratio: float | None     # volume / avg_volume
    body_to_range_ratio: float | None
    body_atr_ratio: float | None
    has_rejection_wick: bool | None

    # ── ATR / price context ─────────────────────────────────────────────
    atr: float | None
    atr_pct: float | None
    rsi: float | None

    # ── Overextension (Stage 6a) ─────────────────────────────────────────
    distance_from_ema_atr: float | None  # (close - EMA20) / ATR

    # ── Cooldown (Stage 6b) ─────────────────────────────────────────────
    cooldown_active: bool
    cooldown_candles_remaining: int
    last_trade_result: str | None        # "win" | "loss" | None

    # ── Trade frequency (Stage 6c) ───────────────────────────────────────
    frequency_limit_active: bool
    trades_in_window: int
    minutes_since_last_entry: float | None

    # ── Execution fields (approved signals only) ─────────────────────────
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    quantity: float | None
    risk_amount_usdt: float | None
    reward_amount_usdt: float | None
    risk_distance: float | None


# ─────────────────────────────────────────────────────────────────────────────
# Rejection helpers
# ─────────────────────────────────────────────────────────────────────────────

def _reject(base: dict, code: str, detail: str) -> SignalEvaluation:
    """Build a rejected SignalEvaluation at any stage."""
    summary = f"REJECTED [{code}]: {detail}"
    logger.info(
        "%s: %s | candle=%s",
        base["symbol"], summary, base.get("candle_timestamp", "?"),
    )
    return SignalEvaluation(
        symbol=base["symbol"],
        evaluated_at=base["evaluated_at"],
        candle_timestamp=base.get("candle_timestamp", ""),
        approved=False,
        rejection_code=code,
        rejection_reason=detail,
        summary=summary,
        direction=base.get("direction"),
        trend_state=base.get("trend_state", "unknown"),
        ema_spread_pct=base.get("ema_spread_pct"),
        regime_label=base.get("regime_label", "unknown"),
        regime_score=base.get("regime_score"),
        regime_atr_expanding=base.get("regime_atr_expanding"),
        breakout_level=base.get("breakout_level"),
        close_vs_level=base.get("close_vs_level"),
        close_buffer_atr=base.get("close_buffer_atr"),
        volume_ratio=base.get("volume_ratio"),
        body_to_range_ratio=base.get("body_to_range_ratio"),
        body_atr_ratio=base.get("body_atr_ratio"),
        has_rejection_wick=base.get("has_rejection_wick"),
        atr=base.get("atr"),
        atr_pct=base.get("atr_pct"),
        rsi=base.get("rsi"),
        distance_from_ema_atr=base.get("distance_from_ema_atr"),
        cooldown_active=base.get("cooldown_active", False),
        cooldown_candles_remaining=base.get("cooldown_candles_remaining", 0),
        last_trade_result=base.get("last_trade_result"),
        frequency_limit_active=base.get("frequency_limit_active", False),
        trades_in_window=base.get("trades_in_window", 0),
        minutes_since_last_entry=base.get("minutes_since_last_entry"),
        entry_price=None, stop_loss=None, take_profit=None,
        quantity=None, risk_amount_usdt=None,
        reward_amount_usdt=None, risk_distance=None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Per-stage helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_trend(df_1h: pd.DataFrame) -> tuple[str, float | None]:
    """
    Stage 2: Determine 1h trend direction.
    Returns (trend_label, ema_spread_pct).
    """
    required = ["ema_fast", "ema_slow", "ema_spread_pct"]
    valid = df_1h.dropna(subset=required)
    if valid.empty:
        return "neutral", None

    row = valid.iloc[-1]
    spread = float(row["ema_spread_pct"])

    if spread < settings.EMA_MIN_SPREAD_PCT:
        return "neutral", spread

    ema_fast = float(row["ema_fast"])
    ema_slow = float(row["ema_slow"])

    if ema_fast > ema_slow:
        return "bullish", spread
    elif ema_fast < ema_slow:
        return "bearish", spread
    return "neutral", spread


def _check_cooldown(
    symbol: str,
    store: "TradeStore",
    current_time: datetime | None = None,
) -> tuple[bool, int, str | None]:
    """
    Stage 6b: Check if symbol is in cooldown based on time elapsed since last trade close.
    Returns (active, candles_remaining, last_result).
    last_result: "win" | "loss" | None

    Args:
        current_time: Reference time for elapsed calculation. Defaults to
                      datetime.now(UTC). Pass the candle timestamp during
                      backtesting to avoid using real wall-clock time.
    """
    last = store.get_last_closed_trade(symbol)
    if last is None:
        return False, 0, None

    was_loss = last.status == "closed_sl"
    last_result = "loss" if was_loss else "win"
    required_candles = settings.LOSS_COOLDOWN_CANDLES if was_loss else settings.WIN_COOLDOWN_CANDLES

    try:
        closed_at = datetime.fromisoformat(last.closed_at)
    except (ValueError, TypeError):
        return False, 0, last_result

    # Ensure timezone-aware comparison
    if closed_at.tzinfo is None:
        closed_at = closed_at.replace(tzinfo=timezone.utc)

    now = current_time if current_time is not None else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    elapsed_minutes = (now - closed_at).total_seconds() / 60.0
    elapsed_candles = int(elapsed_minutes / settings.ENTRY_CANDLE_MINUTES)

    remaining = required_candles - elapsed_candles
    if remaining > 0:
        return True, remaining, last_result

    return False, 0, last_result


def _check_frequency(
    symbol: str,
    store: "TradeStore",
    current_time: datetime | None = None,
) -> tuple[bool, int, float | None]:
    """
    Stage 6c: Check trade frequency limits.
    Returns (limit_active, trades_in_window, minutes_since_last_entry).

    Args:
        current_time: Reference time for the rolling window. Defaults to
                      datetime.now(UTC). Pass the candle timestamp during
                      backtesting to avoid using real wall-clock time.
    """
    now = current_time if current_time is not None else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    window_start = now - timedelta(minutes=settings.TRADE_WINDOW_MINUTES)

    entry_times = store.get_recent_entry_times(symbol, since=window_start)
    trades_in_window = len(entry_times)

    minutes_since_last: float | None = None
    if entry_times:
        last_entry = entry_times[-1]
        if last_entry.tzinfo is None:
            last_entry = last_entry.replace(tzinfo=timezone.utc)
        minutes_since_last = (now - last_entry).total_seconds() / 60.0

    limit_active = False
    if trades_in_window >= settings.MAX_TRADES_PER_WINDOW:
        limit_active = True
    elif minutes_since_last is not None and minutes_since_last < settings.MIN_ENTRY_GAP_MINUTES:
        limit_active = True

    return limit_active, trades_in_window, minutes_since_last


def _compute_risk(
    direction: str,
    entry_price: float,
    atr: float,
    balance_usdt: float,
) -> tuple[float, float, float, float, float, float]:
    """
    Compute SL, TP, risk_distance, quantity, risk_amount, reward_amount.
    Returns (stop_loss, take_profit, risk_distance, quantity, risk_amount, reward_amount).
    Returns all-zero tuple if invalid.
    """
    if balance_usdt <= 0 or atr <= 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

    risk_distance = atr * settings.ATR_STOP_MULTIPLIER
    if risk_distance <= 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

    risk_amount = balance_usdt * (settings.RISK_PERCENT / 100.0)

    if direction == "long":
        stop_loss = entry_price - risk_distance
        take_profit = entry_price + risk_distance * settings.REWARD_TO_RISK
    else:
        stop_loss = entry_price + risk_distance
        take_profit = entry_price - risk_distance * settings.REWARD_TO_RISK

    if stop_loss <= 0 or take_profit <= 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

    quantity = risk_amount / risk_distance
    position_cost = quantity * entry_price
    if position_cost > balance_usdt:
        quantity = balance_usdt / entry_price

    reward_amount = risk_amount * settings.REWARD_TO_RISK

    return stop_loss, take_profit, risk_distance, quantity, risk_amount, reward_amount


# ─────────────────────────────────────────────────────────────────────────────
# Main evaluation function
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_signal(
    symbol: str,
    df_1h: pd.DataFrame,
    df_15m: pd.DataFrame,
    store: "TradeStore",
    balance_usdt: float,
    current_time: datetime | None = None,
) -> SignalEvaluation:
    """
    Run all 7 evaluation stages for one symbol and return a SignalEvaluation.

    Args:
        current_time: Reference time for cooldown and frequency checks.
                      Defaults to datetime.now(UTC).
                      Pass the candle's own timestamp when running backtests
                      so cooldown/frequency logic uses simulated time, not
                      real wall-clock time.
    """
    evaluated_at = (
        current_time.isoformat()
        if current_time is not None
        else datetime.now(timezone.utc).isoformat()
    )
    base: dict = {
        "symbol": symbol,
        "evaluated_at": evaluated_at,
        "trend_state": "unknown",
        "regime_label": "unknown",
        "cooldown_active": False,
        "cooldown_candles_remaining": 0,
        "frequency_limit_active": False,
        "trades_in_window": 0,
    }

    # ── Stage 1: Data sufficiency ─────────────────────────────────────────────
    required_15m = [
        "close", "open", "high", "low", "volume",
        "rsi", "atr", "atr_pct", "avg_volume",
        "ema_fast", "ema_slow", "ema_spread_pct",
        "swing_high", "swing_low",
        "body_to_range", "body_atr_ratio",
        "upper_wick", "lower_wick",
        "dist_from_ema_fast_atr",
    ]
    df_15m_valid = df_15m.dropna(subset=required_15m)
    df_1h_valid = df_1h.dropna(subset=["ema_fast", "ema_slow", "ema_spread_pct"])

    if df_1h_valid.empty:
        return _reject(base, "NO_1H_DATA", "Insufficient 1h indicator data")
    if df_15m_valid.empty:
        return _reject(base, "NO_15M_DATA", "Insufficient 15m indicator data")

    latest = df_15m_valid.iloc[-1]
    candle_ts = str(latest.name)
    base["candle_timestamp"] = candle_ts

    close = float(latest["close"])
    open_ = float(latest["open"])
    high = float(latest["high"])
    swing_high = float(latest["swing_high"])
    swing_low = float(latest["swing_low"])
    volume = float(latest["volume"])
    avg_volume = float(latest["avg_volume"])
    atr = float(latest["atr"])
    atr_pct = float(latest["atr_pct"])
    rsi = float(latest["rsi"])
    body_to_range = float(latest["body_to_range"]) if not pd.isna(latest["body_to_range"]) else 0.0
    body_atr_ratio = float(latest["body_atr_ratio"]) if not pd.isna(latest["body_atr_ratio"]) else 0.0
    dist_from_ema_atr = float(latest["dist_from_ema_fast_atr"]) if not pd.isna(latest["dist_from_ema_fast_atr"]) else 0.0
    upper_wick = float(latest["upper_wick"])
    lower_wick = float(latest["lower_wick"])
    candle_body = abs(close - open_)

    base.update({
        "atr": atr, "atr_pct": atr_pct, "rsi": rsi,
        "body_to_range_ratio": body_to_range,
        "body_atr_ratio": body_atr_ratio,
        "distance_from_ema_atr": dist_from_ema_atr,
    })

    # ATR sanity check
    if atr <= 0 or atr_pct < settings.ATR_MIN_PCT:
        return _reject(
            base, "ATR_INVALID",
            f"ATR={atr:.6f} atr_pct={atr_pct:.4f}% < min={settings.ATR_MIN_PCT}%"
        )

    if avg_volume <= 0:
        return _reject(base, "AVG_VOL_INVALID", "Average volume is zero")

    # ── Stage 2: Trend determination ─────────────────────────────────────────
    trend_state, ema_spread_pct = _get_trend(df_1h)
    base["trend_state"] = trend_state
    base["ema_spread_pct"] = ema_spread_pct

    if trend_state == "neutral":
        reason = (
            f"EMA spread={ema_spread_pct:.3f}% < min={settings.EMA_MIN_SPREAD_PCT}%"
            if ema_spread_pct is not None
            else "EMA20 ≈ EMA50 — no clear trend"
        )
        return _reject(base, "NEUTRAL_TREND", reason)

    # direction determined by trend
    direction: Direction = "long" if trend_state == "bullish" else "short"
    base["direction"] = direction

    # ── Stage 3: Regime classification ────────────────────────────────────────
    regime: RegimeResult = classify_regime(
        df_1h=df_1h_valid,
        direction=trend_state,
        min_trend_score=settings.REGIME_MIN_TREND_SCORE,
    )
    base["regime_label"] = regime.label
    base["regime_score"] = regime.score
    base["regime_atr_expanding"] = regime.atr_expanding

    if regime.label != "trending":
        return _reject(
            base, "REGIME_UNFAVORABLE",
            f"Regime={regime.label} score={regime.score:.3f} "
            f"(need >={settings.REGIME_MIN_TREND_SCORE:.2f} for 'trending'). "
            f"spread={regime.ema_spread_pct:.3f}% slope={regime.ema_fast_slope_pct:.4f}% "
            f"atr_exp={regime.atr_expanding} aligned="
            f"{regime.price_above_both_emas if direction=='long' else regime.price_below_both_emas}"
        )

    # ── Stage 4: Breakout candidate detection ────────────────────────────────
    breakout_level: float
    close_vs_level: float

    if direction == "long":
        breakout_level = swing_high
        close_vs_level = close - swing_high
    else:
        breakout_level = swing_low
        close_vs_level = swing_low - close

    base["breakout_level"] = breakout_level
    base["close_vs_level"] = close_vs_level

    if close_vs_level <= 0:
        return _reject(
            base, "NO_BREAKOUT",
            f"{direction}: close={close:.4f} did not break "
            f"{'swing_high' if direction=='long' else 'swing_low'}={breakout_level:.4f}"
        )

    close_buffer_atr = close_vs_level / atr if atr > 0 else 0.0
    base["close_buffer_atr"] = close_buffer_atr

    # ── Stage 5: Breakout quality ─────────────────────────────────────────────

    # 5a. Close must exceed breakout level by meaningful buffer
    min_buffer_atr = settings.BREAKOUT_CLOSE_BUFFER_RATIO
    if close_buffer_atr < min_buffer_atr:
        return _reject(
            base, "WEAK_BREAKOUT_BUFFER",
            f"close_buffer={close_buffer_atr:.4f} ATR < min={min_buffer_atr:.4f} ATR. "
            f"Close only {close_vs_level:.4f} beyond level — wick or noise, not a real break."
        )

    # 5b. Volume must confirm breakout
    volume_ratio = volume / avg_volume if avg_volume > 0 else 0.0
    base["volume_ratio"] = volume_ratio

    if volume_ratio < settings.VOLUME_RATIO_THRESHOLD:
        return _reject(
            base, "VOLUME_INSUFFICIENT",
            f"volume_ratio={volume_ratio:.3f} < threshold={settings.VOLUME_RATIO_THRESHOLD:.2f} "
            f"(vol={volume:.2f} avg={avg_volume:.2f})"
        )

    # 5c. Candle body quality — reject wick-dominated candles
    if body_to_range < settings.MIN_BODY_TO_RANGE_RATIO:
        return _reject(
            base, "WICK_REJECTION_CANDLE",
            f"body_to_range={body_to_range:.3f} < min={settings.MIN_BODY_TO_RANGE_RATIO:.2f}. "
            f"Candle is wick-heavy — likely a rejection, not a clean breakout."
        )

    # 5d. Rejection wick check: upper wick > 2x body for longs, lower wick > 2x body for shorts
    rejection_wick = False
    if direction == "long" and upper_wick > candle_body * 2:
        rejection_wick = True
    elif direction == "short" and lower_wick > candle_body * 2:
        rejection_wick = True
    base["has_rejection_wick"] = rejection_wick

    if rejection_wick:
        return _reject(
            base, "REJECTION_WICK",
            f"{direction}: candle shows rejection wick. "
            f"{'upper' if direction == 'long' else 'lower'}_wick={upper_wick if direction=='long' else lower_wick:.4f} "
            f"> 2 × body={candle_body:.4f}. Price likely rejecting the breakout."
        )

    # ── Stage 6a: RSI extremes ────────────────────────────────────────────────
    if direction == "long" and rsi >= settings.RSI_OVERBOUGHT:
        return _reject(
            base, "RSI_OVERBOUGHT",
            f"RSI={rsi:.2f} >= {settings.RSI_OVERBOUGHT} — long entry is overextended on momentum."
        )
    if direction == "short" and rsi <= settings.RSI_OVERSOLD:
        return _reject(
            base, "RSI_OVERSOLD",
            f"RSI={rsi:.2f} <= {settings.RSI_OVERSOLD} — short entry is overextended on momentum."
        )

    # ── Stage 6a: Overextension — candle body size ────────────────────────────
    if body_atr_ratio > settings.MAX_BODY_ATR_RATIO:
        return _reject(
            base, "ENTRY_CANDLE_TOO_LARGE",
            f"body_atr_ratio={body_atr_ratio:.3f} > max={settings.MAX_BODY_ATR_RATIO}. "
            f"Entry candle is {body_atr_ratio:.1f}x ATR — chasing a large move."
        )

    # ── Stage 6a: Overextension — price too far from EMA ─────────────────────
    if dist_from_ema_atr > settings.MAX_DISTANCE_FROM_EMA_ATR_RATIO:
        return _reject(
            base, "PRICE_OVEREXTENDED",
            f"dist_from_EMA20={dist_from_ema_atr:.3f} ATR > max={settings.MAX_DISTANCE_FROM_EMA_ATR_RATIO}. "
            f"Price is too extended from the mean — risky late entry."
        )

    # ── Stage 6b: Cooldown check ──────────────────────────────────────────────
    cooldown_active, cooldown_remaining, last_result = _check_cooldown(
        symbol, store, current_time
    )
    base["cooldown_active"] = cooldown_active
    base["cooldown_candles_remaining"] = cooldown_remaining
    base["last_trade_result"] = last_result

    if cooldown_active:
        return _reject(
            base, "COOLDOWN_ACTIVE",
            f"Symbol in cooldown after last {last_result}. "
            f"{cooldown_remaining} more 15m candle(s) required before re-entry."
        )

    # ── Stage 6c: Trade frequency check ──────────────────────────────────────
    freq_active, trades_in_window, mins_since_last = _check_frequency(
        symbol, store, current_time
    )
    base["frequency_limit_active"] = freq_active
    base["trades_in_window"] = trades_in_window
    base["minutes_since_last_entry"] = mins_since_last

    if freq_active:
        if trades_in_window >= settings.MAX_TRADES_PER_WINDOW:
            freq_reason = (
                f"{trades_in_window} trades in last {settings.TRADE_WINDOW_MINUTES}min "
                f">= max={settings.MAX_TRADES_PER_WINDOW}. Overtrading limit hit."
            )
        else:
            freq_reason = (
                f"Only {mins_since_last:.1f}min since last entry — "
                f"minimum gap is {settings.MIN_ENTRY_GAP_MINUTES}min."
            )
        return _reject(base, "FREQUENCY_LIMIT", freq_reason)

    # ── Stage 7: All checks passed — compute risk and build approval ──────────
    entry_price = close
    sl, tp, risk_dist, qty, risk_amt, reward_amt = _compute_risk(
        direction, entry_price, atr, balance_usdt
    )

    if sl == 0 or tp == 0 or qty == 0:
        return _reject(
            base, "RISK_CALC_FAILED",
            f"Risk calculation produced invalid parameters. "
            f"entry={entry_price:.4f} atr={atr:.6f} balance={balance_usdt:.2f}"
        )

    summary = (
        f"APPROVED [{direction.upper()}] entry={entry_price:.4f} SL={sl:.4f} TP={tp:.4f} "
        f"qty={qty:.6f} regime={regime.label}({regime.score:.2f}) "
        f"vol_ratio={volume_ratio:.2f} body/range={body_to_range:.2f}"
    )

    logger.info(
        "%s: %s | candle=%s | risk=%.2f USDT reward=%.2f USDT | ATR=%.4f(%.3f%%)",
        symbol, summary, candle_ts, risk_amt, reward_amt, atr, atr_pct,
    )

    return SignalEvaluation(
        symbol=symbol,
        evaluated_at=evaluated_at,
        candle_timestamp=candle_ts,
        approved=True,
        rejection_code=None,
        rejection_reason=None,
        summary=summary,
        direction=direction,
        trend_state=trend_state,
        ema_spread_pct=ema_spread_pct,
        regime_label=regime.label,
        regime_score=regime.score,
        regime_atr_expanding=regime.atr_expanding,
        breakout_level=breakout_level,
        close_vs_level=close_vs_level,
        close_buffer_atr=close_buffer_atr,
        volume_ratio=volume_ratio,
        body_to_range_ratio=body_to_range,
        body_atr_ratio=body_atr_ratio,
        has_rejection_wick=False,
        atr=atr,
        atr_pct=atr_pct,
        rsi=rsi,
        distance_from_ema_atr=dist_from_ema_atr,
        cooldown_active=False,
        cooldown_candles_remaining=0,
        last_trade_result=last_result,
        frequency_limit_active=False,
        trades_in_window=trades_in_window,
        minutes_since_last_entry=mins_since_last,
        entry_price=entry_price,
        stop_loss=sl,
        take_profit=tp,
        quantity=qty,
        risk_amount_usdt=risk_amt,
        reward_amount_usdt=reward_amt,
        risk_distance=risk_dist,
    )


def evaluate_all_signals(
    enriched: dict[str, dict[str, pd.DataFrame]],
    store: "TradeStore",
    balance_usdt: float,
    timeframe_trend: str = "1h",
    timeframe_entry: str = "15m",
) -> list[SignalEvaluation]:
    """
    Evaluate all configured symbols in one call.
    Returns a list of SignalEvaluation objects (approved and rejected).
    """
    results: list[SignalEvaluation] = []

    for symbol, timeframes in enriched.items():
        df_1h = timeframes.get(timeframe_trend)
        df_15m = timeframes.get(timeframe_entry)

        if df_1h is None or df_15m is None:
            logger.warning(
                "%s: Missing timeframe data (%s or %s). Skipping.",
                symbol, timeframe_trend, timeframe_entry,
            )
            continue

        result = evaluate_signal(symbol, df_1h, df_15m, store, balance_usdt)
        results.append(result)

    approved = [r for r in results if r.approved]
    rejected = [r for r in results if not r.approved]

    logger.info(
        "Signal scan: %d approved / %d rejected. Approved: %s | Rejection codes: %s",
        len(approved), len(rejected),
        [(r.symbol, r.direction) for r in approved] or "none",
        {r.symbol: r.rejection_code for r in rejected},
    )

    return results
