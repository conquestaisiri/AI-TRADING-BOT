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
    Add indicator columns to the OHLCV DataFrame in-place.
    Columns added:
      ema_fast, ema_slow, rsi, atr, avg_volume, swing_high, swing_low
    Returns the enriched DataFrame.
    """
    df = df.copy()

    df["ema_fast"] = _ema(df["close"], ema_fast)
    df["ema_slow"] = _ema(df["close"], ema_slow)
    df["rsi"] = _rsi(df["close"], rsi_period)
    df["atr"] = _atr(df, atr_period)
    df["avg_volume"] = df["volume"].rolling(window=volume_avg_period).mean()

    df["swing_high"] = df["high"].rolling(window=swing_lookback).max()
    df["swing_low"] = df["low"].rolling(window=swing_lookback).min()

    min_rows_needed = max(ema_slow, rsi_period, atr_period, volume_avg_period, swing_lookback)
    valid_rows = df.iloc[min_rows_needed:]
    if valid_rows.empty:
        logger.warning(
            "Not enough rows to compute all indicators. Need at least %d rows, got %d.",
            min_rows_needed,
            len(df),
        )

    logger.debug(
        "Indicators calculated. Latest row: EMA%d=%.4f, EMA%d=%.4f, RSI=%.2f, ATR=%.4f",
        ema_fast,
        df["ema_fast"].iloc[-1],
        ema_slow,
        df["ema_slow"].iloc[-1],
        df["rsi"].iloc[-1],
        df["atr"].iloc[-1],
    )

    return df


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
