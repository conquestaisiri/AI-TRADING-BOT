from dataclasses import dataclass
from typing import Literal
import pandas as pd
from config.settings import settings
from logs.logger import get_logger

logger = get_logger("strategy.signal")

Direction = Literal["long", "short"]

REJECTION_REASONS = {
    "NO_1H_DATA": "No valid 1h indicator data",
    "NO_15M_DATA": "No valid 15m indicator data",
    "NEUTRAL_TREND": "1h trend is neutral (EMA20 ≈ EMA50)",
    "WEAK_TREND": "EMA spread too narrow — trend not strong enough",
    "ATR_INVALID": "ATR is zero or below minimum threshold (choppy market)",
    "AVG_VOL_INVALID": "Average volume is zero or invalid",
    "NO_BREAKOUT": "Price did not break swing level",
    "BELOW_AVG_VOL": "Volume is not above average — no confirmation",
    "RSI_OVERBOUGHT": "RSI overbought — long entry too extended to chase",
    "RSI_OVERSOLD": "RSI oversold — short entry too extended to chase",
}


@dataclass
class Setup:
    symbol: str
    direction: Direction
    entry_price: float
    atr: float
    atr_pct: float
    swing_high: float
    swing_low: float
    rsi: float
    volume: float
    avg_volume: float
    ema_spread_pct: float
    candle_timestamp: str
    trend_1h: str


@dataclass
class RejectionRecord:
    symbol: str
    reason_code: str
    reason_detail: str
    candle_timestamp: str


def _trend_direction(df_1h: pd.DataFrame, min_spread_pct: float) -> tuple[str, str]:
    """
    Determine 1h trend from the most recent valid candle.
    Returns (trend_label, rejection_reason_or_empty).

    trend_label: 'bullish' | 'bearish' | 'neutral'
    rejection: '' if valid trend, else rejection code
    """
    valid = df_1h.dropna(subset=["ema_fast", "ema_slow", "ema_spread_pct"])
    if valid.empty:
        return "neutral", "NO_1H_DATA"

    row = valid.iloc[-1]
    spread_pct = float(row["ema_spread_pct"])

    if spread_pct < min_spread_pct:
        return "neutral", "WEAK_TREND"

    if float(row["ema_fast"]) > float(row["ema_slow"]):
        return "bullish", ""
    elif float(row["ema_fast"]) < float(row["ema_slow"]):
        return "bearish", ""

    return "neutral", "NEUTRAL_TREND"


def _reject(
    symbol: str,
    candle_ts: str,
    code: str,
    extra: str = "",
) -> RejectionRecord:
    """
    Build and log a rejection record.
    """
    detail = REJECTION_REASONS.get(code, code)
    if extra:
        detail = f"{detail} ({extra})"
    logger.info(
        "%s: SKIP — %s | %s | candle=%s",
        symbol, code, detail, candle_ts,
    )
    return RejectionRecord(
        symbol=symbol,
        reason_code=code,
        reason_detail=detail,
        candle_timestamp=candle_ts,
    )


def detect_setup(
    symbol: str,
    df_1h: pd.DataFrame,
    df_15m: pd.DataFrame,
) -> Setup | RejectionRecord:
    """
    Evaluate a trading setup for a symbol using enriched 1h and 15m DataFrames.

    Returns a Setup if all conditions pass.
    Returns a RejectionRecord with the specific reason if any condition fails.

    Long setup (all must be true):
        - 1h EMA20 > EMA50 with spread >= min_spread_pct (real trend, not noise)
        - 15m close > prior N-bar swing high (confirmed break, not self-referential)
        - 15m volume > 15m avg volume (confirmed by participation)
        - 15m RSI < RSI_OVERBOUGHT (not chasing an extended move)
        - 15m ATR > ATR_MIN_PCT of price (not in dead, choppy conditions)

    Short setup (all must be true):
        - 1h EMA20 < EMA50 with spread >= min_spread_pct
        - 15m close < prior N-bar swing low (confirmed break)
        - 15m volume > 15m avg volume
        - 15m RSI > RSI_OVERSOLD (not chasing an extended move down)
        - 15m ATR > ATR_MIN_PCT of price
    """
    # --- 1h trend check ---
    trend, rejection_code = _trend_direction(df_1h, settings.EMA_MIN_SPREAD_PCT)
    candle_ts_1h = str(df_1h.dropna().index[-1]) if not df_1h.dropna().empty else "unknown"

    if rejection_code:
        return _reject(symbol, candle_ts_1h, rejection_code)

    # --- 15m data validation ---
    required_cols = ["close", "volume", "rsi", "atr", "atr_pct", "avg_volume", "swing_high", "swing_low", "ema_spread_pct"]
    df_15m_valid = df_15m.dropna(subset=required_cols)

    if df_15m_valid.empty:
        return _reject(symbol, "unknown", "NO_15M_DATA")

    latest = df_15m_valid.iloc[-1]
    candle_ts = str(latest.name)

    close = float(latest["close"])
    swing_high = float(latest["swing_high"])
    swing_low = float(latest["swing_low"])
    volume = float(latest["volume"])
    avg_volume = float(latest["avg_volume"])
    atr = float(latest["atr"])
    atr_pct = float(latest["atr_pct"])
    rsi = float(latest["rsi"])
    ema_spread_pct = float(latest["ema_spread_pct"])

    # --- ATR validity (rejects dead/choppy markets) ---
    if atr <= 0 or pd.isna(atr) or atr_pct < settings.ATR_MIN_PCT:
        return _reject(
            symbol, candle_ts, "ATR_INVALID",
            f"atr={atr:.6f} atr_pct={atr_pct:.4f}% min={settings.ATR_MIN_PCT}%",
        )

    # --- Average volume validity ---
    if avg_volume <= 0 or pd.isna(avg_volume):
        return _reject(symbol, candle_ts, "AVG_VOL_INVALID")

    # --- Volume breakout confirmation ---
    if volume <= avg_volume:
        return _reject(
            symbol, candle_ts, "BELOW_AVG_VOL",
            f"vol={volume:.2f} avg={avg_volume:.2f}",
        )

    # --- Long setup ---
    if trend == "bullish":
        if close <= swing_high:
            return _reject(
                symbol, candle_ts, "NO_BREAKOUT",
                f"long: close={close:.4f} <= swing_high={swing_high:.4f}",
            )
        if rsi >= settings.RSI_OVERBOUGHT:
            return _reject(
                symbol, candle_ts, "RSI_OVERBOUGHT",
                f"rsi={rsi:.2f} threshold={settings.RSI_OVERBOUGHT}",
            )
        logger.info(
            "%s: LONG SETUP | candle=%s | close=%.4f > swing_high=%.4f | "
            "vol=%.2f > avg=%.2f | RSI=%.2f | ATR=%.4f(%.3f%%) | "
            "EMA_spread=%.3f%% | 1h_trend=bullish",
            symbol, candle_ts, close, swing_high,
            volume, avg_volume, rsi, atr, atr_pct, ema_spread_pct,
        )
        return Setup(
            symbol=symbol, direction="long",
            entry_price=close, atr=atr, atr_pct=atr_pct,
            swing_high=swing_high, swing_low=swing_low,
            rsi=rsi, volume=volume, avg_volume=avg_volume,
            ema_spread_pct=ema_spread_pct,
            candle_timestamp=candle_ts, trend_1h=trend,
        )

    # --- Short setup ---
    if trend == "bearish":
        if close >= swing_low:
            return _reject(
                symbol, candle_ts, "NO_BREAKOUT",
                f"short: close={close:.4f} >= swing_low={swing_low:.4f}",
            )
        if rsi <= settings.RSI_OVERSOLD:
            return _reject(
                symbol, candle_ts, "RSI_OVERSOLD",
                f"rsi={rsi:.2f} threshold={settings.RSI_OVERSOLD}",
            )
        logger.info(
            "%s: SHORT SETUP | candle=%s | close=%.4f < swing_low=%.4f | "
            "vol=%.2f > avg=%.2f | RSI=%.2f | ATR=%.4f(%.3f%%) | "
            "EMA_spread=%.3f%% | 1h_trend=bearish",
            symbol, candle_ts, close, swing_low,
            volume, avg_volume, rsi, atr, atr_pct, ema_spread_pct,
        )
        return Setup(
            symbol=symbol, direction="short",
            entry_price=close, atr=atr, atr_pct=atr_pct,
            swing_high=swing_high, swing_low=swing_low,
            rsi=rsi, volume=volume, avg_volume=avg_volume,
            ema_spread_pct=ema_spread_pct,
            candle_timestamp=candle_ts, trend_1h=trend,
        )

    return _reject(symbol, candle_ts, "NEUTRAL_TREND")


def detect_all_setups(
    enriched: dict[str, dict[str, pd.DataFrame]],
    timeframe_trend: str = "1h",
    timeframe_entry: str = "15m",
) -> tuple[list[Setup], list[RejectionRecord]]:
    """
    Run setup detection for all symbols.
    Returns (valid_setups, rejection_records).
    """
    setups: list[Setup] = []
    rejections: list[RejectionRecord] = []

    for symbol, timeframes in enriched.items():
        df_1h = timeframes.get(timeframe_trend)
        df_15m = timeframes.get(timeframe_entry)

        if df_1h is None or df_15m is None:
            logger.warning("%s: Missing timeframe data (%s or %s). Skipping.", symbol, timeframe_trend, timeframe_entry)
            continue

        result = detect_setup(symbol, df_1h, df_15m)

        if isinstance(result, Setup):
            setups.append(result)
        else:
            rejections.append(result)

    logger.info(
        "Scan complete: %d setup(s) found, %d rejected. Setups: %s",
        len(setups),
        len(rejections),
        [(s.symbol, s.direction) for s in setups] if setups else "none",
    )
    return setups, rejections
