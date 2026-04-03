import pandas as pd
import ccxt
from config.settings import settings
from logs.logger import get_logger

logger = get_logger("data.market_data")


def fetch_ohlcv(exchange: ccxt.binance, symbol: str, timeframe: str) -> pd.DataFrame:
    """
    Fetch OHLCV candle data for a symbol and timeframe from the futures testnet.
    Returns a DataFrame with columns: timestamp (index), open, high, low, close, volume.
    Raises RuntimeError on failure.
    """
    if symbol not in exchange.markets:
        raise RuntimeError(
            f"Symbol '{symbol}' not found in exchange markets. "
            f"Available futures pairs must be valid USDT-margined perpetuals. "
            f"Check your SYMBOLS setting in .env."
        )

    try:
        raw = exchange.fetch_ohlcv(
            symbol,
            timeframe=timeframe,
            limit=settings.OHLCV_LIMIT,
        )
    except ccxt.BadSymbol as exc:
        raise RuntimeError(
            f"Symbol '{symbol}' rejected by exchange on fetch: {exc}. "
            f"Ensure you are using futures symbols (e.g. BTCUSDT, not BTC/USDT)."
        ) from exc
    except ccxt.NetworkError as exc:
        raise RuntimeError(
            f"Network error fetching OHLCV for {symbol}/{timeframe}: {exc}"
        ) from exc
    except ccxt.ExchangeError as exc:
        raise RuntimeError(
            f"Exchange error fetching OHLCV for {symbol}/{timeframe}: {exc}"
        ) from exc

    min_required = max(
        settings.EMA_SLOW,
        settings.RSI_PERIOD,
        settings.ATR_PERIOD,
        settings.VOLUME_AVG_PERIOD,
        settings.SWING_LOOKBACK,
    ) + 10  # buffer

    if not raw or len(raw) < min_required:
        raise RuntimeError(
            f"Insufficient OHLCV data for {symbol}/{timeframe}. "
            f"Got {len(raw) if raw else 0} candles, need at least {min_required}. "
            f"Increase OHLCV_LIMIT in your .env."
        )

    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()

    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)

    logger.info(
        "Fetched %d candles | %s [%s] | range: %s → %s | last_close=%.4f",
        len(df),
        symbol,
        timeframe,
        str(df.index[0]),
        str(df.index[-1]),
        df["close"].iloc[-1],
    )

    return df


def fetch_all_ohlcv(exchange: ccxt.binance) -> dict[str, dict[str, pd.DataFrame]]:
    """
    Fetch OHLCV data for all configured symbols and both timeframes.
    Returns: {symbol: {"1h": df, "15m": df}}
    Raises RuntimeError for any symbol/timeframe that cannot be fetched.
    """
    result: dict[str, dict[str, pd.DataFrame]] = {}

    for symbol in settings.SYMBOLS:
        result[symbol] = {}
        for tf in (settings.TIMEFRAME_TREND, settings.TIMEFRAME_ENTRY):
            df = fetch_ohlcv(exchange, symbol, tf)
            result[symbol][tf] = df

    return result
