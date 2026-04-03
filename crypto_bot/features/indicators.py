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
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def calculate_indicators(
    df: pd.DataFrame,
    ema_fast: int = 20,
    ema_slow: int = 50,
    rsi_period: int = 14,
    atr_period: int = 14,
    atr_ma_period: int = 14,
    ema_slope_period: int = 5,
    volume_avg_period: int = 20,
    swing_lookback: int = 20,
) -> pd.DataFrame:
    """
    Enrich an OHLCV DataFrame with all indicator columns.

    Swing high/low uses shift(1) before rolling so the current candle's
    own high/low is NOT included in the reference level it must break.

    Columns added:
        ema_fast, ema_slow, ema_spread_pct
        ema_fast_slope, ema_slow_slope, ema_fast_slope_pct
        rsi
        atr, atr_pct, atr_ma, atr_expanding
        avg_volume
        swing_high, swing_low   (shifted — prior N bars only)
        candle_body, candle_range, body_to_range, body_atr_ratio
        upper_wick, lower_wick
        dist_from_ema_fast_atr  (overextension metric)
    """
    df = df.copy()

    # ── EMAs ──────────────────────────────────────────────────────────────────
    df["ema_fast"] = _ema(df["close"], ema_fast)
    df["ema_slow"] = _ema(df["close"], ema_slow)
    df["ema_spread_pct"] = ((df["ema_fast"] - df["ema_slow"]).abs() / df["ema_slow"]) * 100

    # EMA slope: average change per candle over slope_period bars, normalised to price %
    df["ema_fast_slope"] = df["ema_fast"].diff(ema_slope_period) / ema_slope_period
    df["ema_slow_slope"] = df["ema_slow"].diff(ema_slope_period) / ema_slope_period
    df["ema_fast_slope_pct"] = (df["ema_fast_slope"] / df["close"]) * 100

    # ── RSI ───────────────────────────────────────────────────────────────────
    df["rsi"] = _rsi(df["close"], rsi_period)

    # ── ATR ───────────────────────────────────────────────────────────────────
    df["atr"] = _atr(df, atr_period)
    df["atr_pct"] = (df["atr"] / df["close"]) * 100
    df["atr_ma"] = df["atr"].rolling(window=atr_ma_period).mean()
    df["atr_expanding"] = df["atr"] > df["atr_ma"]  # True when volatility is expanding

    # ── Volume ────────────────────────────────────────────────────────────────
    df["avg_volume"] = df["volume"].rolling(window=volume_avg_period).mean()

    # ── Swing levels (SHIFTED — excludes current candle) ─────────────────────
    # Using shift(1) ensures the current candle's high/low is not part of
    # the reference level it is tested against. This prevents self-reference.
    df["swing_high"] = df["high"].shift(1).rolling(window=swing_lookback).max()
    df["swing_low"] = df["low"].shift(1).rolling(window=swing_lookback).min()

    # ── Candle body / quality metrics ─────────────────────────────────────────
    df["candle_body"] = (df["close"] - df["open"]).abs()
    df["candle_range"] = df["high"] - df["low"]
    df["body_to_range"] = df["candle_body"] / df["candle_range"].replace(0, np.nan)
    df["body_atr_ratio"] = df["candle_body"] / df["atr"].replace(0, np.nan)

    # Wicks (absolute size)
    df["upper_wick"] = df["high"] - df[["close", "open"]].max(axis=1)
    df["lower_wick"] = df[["close", "open"]].min(axis=1) - df["low"]

    # ── Overextension metric ──────────────────────────────────────────────────
    # How many ATR units is price away from EMA20? Used to detect overextension.
    df["dist_from_ema_fast_atr"] = (df["close"] - df["ema_fast"]).abs() / df["atr"].replace(0, np.nan)

    latest = df.iloc[-1]
    logger.debug(
        "Indicators: EMA%d=%.4f EMA%d=%.4f spread=%.3f%% slope_pct=%.4f%% "
        "RSI=%.2f ATR=%.4f(%.3f%%) ATR_MA=%.4f expanding=%s "
        "SwingH=%.4f SwingL=%.4f body/range=%.3f dist_ema_atr=%.2f",
        ema_fast, latest["ema_fast"],
        ema_slow, latest["ema_slow"],
        latest["ema_spread_pct"],
        latest["ema_fast_slope_pct"],
        latest["rsi"],
        latest["atr"], latest["atr_pct"],
        latest["atr_ma"] if not pd.isna(latest["atr_ma"]) else 0,
        bool(latest["atr_expanding"]) if not pd.isna(latest["atr_expanding"]) else False,
        latest["swing_high"] if not pd.isna(latest["swing_high"]) else 0,
        latest["swing_low"] if not pd.isna(latest["swing_low"]) else 0,
        latest["body_to_range"] if not pd.isna(latest["body_to_range"]) else 0,
        latest["dist_from_ema_fast_atr"] if not pd.isna(latest["dist_from_ema_fast_atr"]) else 0,
    )

    return df


def build_feature_summary(df: pd.DataFrame, symbol: str, timeframe: str) -> dict:
    """
    Build a structured feature dictionary from the latest enriched candle.
    Designed to be fed directly into an AI model for regime classification
    and trade scoring (future AI layer hook).
    """
    row = df.dropna(subset=["ema_fast", "ema_slow", "rsi", "atr"]).iloc[-1]

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

    volume_ratio = (
        float(row["volume"]) / float(row["avg_volume"])
        if float(row.get("avg_volume", 0)) > 0 else None
    )

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candle_timestamp": str(row.name),
        "close": float(row["close"]),
        "trend": trend_label,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "ema_spread_pct": float(row["ema_spread_pct"]),
        "ema_fast_slope_pct": float(row.get("ema_fast_slope_pct", 0)),
        "rsi": rsi,
        "rsi_zone": rsi_zone,
        "atr": float(row["atr"]),
        "atr_pct": float(row["atr_pct"]),
        "atr_expanding": bool(row.get("atr_expanding", False)),
        "volume": float(row["volume"]),
        "avg_volume": float(row.get("avg_volume", 0)),
        "volume_ratio": volume_ratio,
        "swing_high": float(row.get("swing_high", 0)),
        "swing_low": float(row.get("swing_low", 0)),
        "body_to_range": float(row.get("body_to_range", 0)),
        "dist_from_ema_fast_atr": float(row.get("dist_from_ema_fast_atr", 0)),
    }


def enrich_all(
    ohlcv_map: dict[str, dict[str, pd.DataFrame]],
    ema_fast: int = 20,
    ema_slow: int = 50,
    rsi_period: int = 14,
    atr_period: int = 14,
    atr_ma_period: int = 14,
    ema_slope_period: int = 5,
    volume_avg_period: int = 20,
    swing_lookback: int = 20,
) -> dict[str, dict[str, pd.DataFrame]]:
    """Apply calculate_indicators to every symbol/timeframe. Returns enriched map."""
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
                atr_ma_period=atr_ma_period,
                ema_slope_period=ema_slope_period,
                volume_avg_period=volume_avg_period,
                swing_lookback=swing_lookback,
            )
    return enriched
