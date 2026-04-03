import pandas as pd
import numpy as np
from logs.logger import get_logger

logger = get_logger("features.indicators")


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def calculate_indicators(
    df: pd.DataFrame,
    ema_fast: int = 20,
    ema_slow: int = 50,
    rsi_period: int = 14,
    atr_period: int = 14,
    volume_avg_period: int = 20,
    swing_lookback: int = 20,
) -> pd.DataFrame:
    """
    Enrich an OHLCV DataFrame with indicator columns.

    Swing high/low uses a SHIFTED window (shift(1)) so the current candle
    is NOT included in its own breakout reference. This prevents self-reference
    where a candle's own high inflates the swing high it must break.

    Columns added:
        ema_fast, ema_slow, ema_spread_pct,
        rsi, atr, atr_pct,
        avg_volume,
        swing_high, swing_low  (shifted — prior N bars only)
    """
    df = df.copy()

    df["ema_fast"] = _ema(df["close"], ema_fast)
    df["ema_slow"] = _ema(df["close"], ema_slow)

    # EMA spread as % of price — used for regime/trend strength check
    df["ema_spread_pct"] = ((df["ema_fast"] - df["ema_slow"]).abs() / df["ema_slow"]) * 100

    df["rsi"] = _rsi(df["close"], rsi_period)
    df["atr"] = _atr(df, atr_period)

    # ATR as % of price — used to detect choppy/low-volatility conditions
    df["atr_pct"] = (df["atr"] / df["close"]) * 100

    df["avg_volume"] = df["volume"].rolling(window=volume_avg_period).mean()

    # CRITICAL FIX: shift(1) before rolling so current candle's high/low is NOT
    # included in the swing level the current candle must break through.
    df["swing_high"] = df["high"].shift(1).rolling(window=swing_lookback).max()
    df["swing_low"] = df["low"].shift(1).rolling(window=swing_lookback).min()

    latest = df.iloc[-1]
    logger.debug(
        "Indicators — EMA%d=%.4f EMA%d=%.4f spread=%.3f%% "
        "RSI=%.2f ATR=%.4f(%.3f%%) SwingH=%.4f SwingL=%.4f",
        ema_fast, latest["ema_fast"],
        ema_slow, latest["ema_slow"],
        latest["ema_spread_pct"],
        latest["rsi"],
        latest["atr"], latest["atr_pct"],
        latest["swing_high"], latest["swing_low"],
    )

    return df


def build_feature_summary(df: pd.DataFrame, symbol: str, timeframe: str) -> dict:
    """
    Build a structured feature dictionary from the latest enriched candle.
    This format is designed to be fed directly into an AI model for regime
    classification and trade scoring (future AI layer hook).
    """
    row = df.dropna().iloc[-1]

    ema_fast = float(row["ema_fast"])
    ema_slow = float(row["ema_slow"])

    if ema_fast > ema_slow:
        trend_label = "bullish"
    elif ema_fast < ema_slow:
        trend_label = "bearish"
    else:
        trend_label = "neutral"

    rsi = float(row["rsi"])
    if rsi > 70:
        rsi_zone = "overbought"
    elif rsi < 30:
        rsi_zone = "oversold"
    elif rsi > 55:
        rsi_zone = "bullish_momentum"
    elif rsi < 45:
        rsi_zone = "bearish_momentum"
    else:
        rsi_zone = "neutral"

    volume_spike = bool(float(row["volume"]) > float(row["avg_volume"]) * 1.5)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candle_timestamp": str(row.name) if hasattr(row, "name") else "unknown",
        "close": float(row["close"]),
        "trend": trend_label,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "ema_spread_pct": float(row["ema_spread_pct"]),
        "rsi": rsi,
        "rsi_zone": rsi_zone,
        "atr": float(row["atr"]),
        "atr_pct": float(row["atr_pct"]),
        "volume": float(row["volume"]),
        "avg_volume": float(row["avg_volume"]),
        "volume_spike": volume_spike,
        "swing_high": float(row["swing_high"]),
        "swing_low": float(row["swing_low"]),
    }


def enrich_all(
    ohlcv_map: dict[str, dict[str, pd.DataFrame]],
    ema_fast: int = 20,
    ema_slow: int = 50,
    rsi_period: int = 14,
    atr_period: int = 14,
    volume_avg_period: int = 20,
    swing_lookback: int = 20,
) -> dict[str, dict[str, pd.DataFrame]]:
    """
    Apply calculate_indicators to every symbol/timeframe combination.
    Returns: {symbol: {timeframe: enriched_df}}
    """
    enriched: dict[str, dict[str, pd.DataFrame]] = {}
    for symbol, timeframes in ohlcv_map.items():
        enriched[symbol] = {}
        for tf, df in timeframes.items():
            logger.info("Calculating indicators for %s [%s]...", symbol, tf)
            enriched[symbol][tf] = calculate_indicators(
                df,
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                rsi_period=rsi_period,
                atr_period=atr_period,
                volume_avg_period=volume_avg_period,
                swing_lookback=swing_lookback,
            )
    return enriched
