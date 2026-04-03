import pandas as pd
import ccxt
from config.settings import settings
from logs.logger import get_logger

logger = get_logger("data.market_data")


def fetch_ohlcv(exchange: ccxt.binance, symbol: str, timeframe: str) -> pd.DataFrame:
    """
    Fetch OHLCV candle data for a symbol and timeframe.
    Returns a DataFrame with columns: timestamp, open, high, low, close, volume.
    Raises RuntimeError on failure.
    """
    if symbol not in exchange.markets:
        raise RuntimeError(
            f"Symbol '{symbol}' not found on exchange. "
            f"Available symbols must be valid Binance testnet pairs."
        )

    try:
        raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=settings.OHLCV_LIMIT)
    except ccxt.NetworkError as exc:
        raise RuntimeError(f"Network error fetching OHLCV for {symbol}/{timeframe}: {exc}") from exc
    except ccxt.ExchangeError as exc:
        raise RuntimeError(f"Exchange error fetching OHLCV for {symbol}/{timeframe}: {exc}") from exc

    if not raw or len(raw) < 2:
        raise RuntimeError(
            f"Insufficient OHLCV data returned for {symbol}/{timeframe}. Got {len(raw)} candles."
        )

    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()

    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)

    logger.debug(
        "Fetched %d candles for %s [%s]. Latest close: %.4f",
        len(df),
        symbol,
        timeframe,
        df["close"].iloc[-1],
    )
    return df


def fetch_all_ohlcv(exchange: ccxt.binance) -> dict[str, dict[str, pd.DataFrame]]:
    """
    Fetch OHLCV data for all configured symbols and both timeframes.
    Returns: {symbol: {"1h": df, "15m": df}}
    """
    result: dict[str, dict[str, pd.DataFrame]] = {}

    for symbol in settings.SYMBOLS:
        logger.info("Fetching market data for %s...", symbol)
        result[symbol] = {}
        for tf in (settings.TIMEFRAME_TREND, settings.TIMEFRAME_ENTRY):
            df = fetch_ohlcv(exchange, symbol, tf)
            result[symbol][tf] = df
            logger.info(
                "  %s [%s]: %d candles, latest close = %.4f",
                symbol,
                tf,
                len(df),
                df["close"].iloc[-1],
            )

    return result
