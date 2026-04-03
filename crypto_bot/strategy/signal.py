from dataclasses import dataclass
from typing import Literal
import pandas as pd
from logs.logger import get_logger

logger = get_logger("strategy.signal")

Direction = Literal["long", "short"]


@dataclass
class Setup:
    symbol: str
    direction: Direction
    entry_price: float
    atr: float
    swing_high: float
    swing_low: float
    rsi: float
    volume: float
    avg_volume: float


def _trend_direction(df_1h: pd.DataFrame) -> Literal["bullish", "bearish", "neutral"]:
    """
    Determine 1h trend using EMA20/EMA50 crossover on the most recent valid candle.
    Returns 'bullish', 'bearish', or 'neutral'.
    """
    row = df_1h.dropna(subset=["ema_fast", "ema_slow"]).iloc[-1]
    ema_fast = float(row["ema_fast"])
    ema_slow = float(row["ema_slow"])

    if ema_fast > ema_slow:
        return "bullish"
    elif ema_fast < ema_slow:
        return "bearish"
    return "neutral"


def detect_setup(
    symbol: str,
    df_1h: pd.DataFrame,
    df_15m: pd.DataFrame,
) -> Setup | None:
    """
    Detect a breakout/continuation trade setup for the given symbol.

    Long setup:
      - 1h trend is bullish (EMA20 > EMA50 on 1h)
      - 15m latest close > recent swing high
      - 15m volume > average volume

    Short setup:
      - 1h trend is bearish (EMA20 < EMA50 on 1h)
      - 15m latest close < recent swing low
      - 15m volume > average volume

    Returns a Setup dataclass if a valid setup is found, else None.
    """
    trend = _trend_direction(df_1h)
    logger.debug("%s: 1h trend is %s", symbol, trend)

    if trend == "neutral":
        logger.info("%s: No trade — trend is neutral (EMA20 == EMA50 on 1h).", symbol)
        return None

    df_15m_valid = df_15m.dropna(subset=["ema_fast", "ema_slow", "rsi", "atr", "avg_volume", "swing_high", "swing_low"])
    if df_15m_valid.empty:
        logger.warning("%s: Not enough 15m data to evaluate setup.", symbol)
        return None

    latest = df_15m_valid.iloc[-1]

    close = float(latest["close"])
    swing_high = float(latest["swing_high"])
    swing_low = float(latest["swing_low"])
    volume = float(latest["volume"])
    avg_volume = float(latest["avg_volume"])
    atr = float(latest["atr"])
    rsi = float(latest["rsi"])

    if atr <= 0 or pd.isna(atr):
        logger.info("%s: No trade — ATR is zero or invalid (%.6f).", symbol, atr)
        return None

    if avg_volume <= 0 or pd.isna(avg_volume):
        logger.info("%s: No trade — average volume is zero or invalid.", symbol)
        return None

    above_avg_volume = volume > avg_volume

    if trend == "bullish" and close > swing_high and above_avg_volume:
        logger.info(
            "%s: LONG setup detected. Close=%.4f > SwingHigh=%.4f, "
            "Volume=%.2f > AvgVolume=%.2f, RSI=%.2f, ATR=%.4f",
            symbol, close, swing_high, volume, avg_volume, rsi, atr,
        )
        return Setup(
            symbol=symbol,
            direction="long",
            entry_price=close,
            atr=atr,
            swing_high=swing_high,
            swing_low=swing_low,
            rsi=rsi,
            volume=volume,
            avg_volume=avg_volume,
        )

    if trend == "bearish" and close < swing_low and above_avg_volume:
        logger.info(
            "%s: SHORT setup detected. Close=%.4f < SwingLow=%.4f, "
            "Volume=%.2f > AvgVolume=%.2f, RSI=%.2f, ATR=%.4f",
            symbol, close, swing_low, volume, avg_volume, rsi, atr,
        )
        return Setup(
            symbol=symbol,
            direction="short",
            entry_price=close,
            atr=atr,
            swing_high=swing_high,
            swing_low=swing_low,
            rsi=rsi,
            volume=volume,
            avg_volume=avg_volume,
        )

    logger.info(
        "%s: No setup. Trend=%s, Close=%.4f, SwingHigh=%.4f, SwingLow=%.4f, "
        "Volume=%.2f, AvgVolume=%.2f",
        symbol, trend, close, swing_high, swing_low, volume, avg_volume,
    )
    return None


def detect_all_setups(
    enriched: dict[str, dict[str, pd.DataFrame]],
    timeframe_trend: str = "1h",
    timeframe_entry: str = "15m",
) -> list[Setup]:
    """
    Run setup detection for all symbols.
    Returns a list of valid Setup objects.
    """
    setups: list[Setup] = []
    for symbol, timeframes in enriched.items():
        df_1h = timeframes.get(timeframe_trend)
        df_15m = timeframes.get(timeframe_entry)

        if df_1h is None or df_15m is None:
            logger.warning("%s: Missing timeframe data, skipping.", symbol)
            continue

        setup = detect_setup(symbol, df_1h, df_15m)
        if setup is not None:
            setups.append(setup)

    logger.info(
        "Setup scan complete. %d candidate setup(s) found: %s",
        len(setups),
        [s.symbol + "/" + s.direction for s in setups] if setups else "none",
    )
    return setups
