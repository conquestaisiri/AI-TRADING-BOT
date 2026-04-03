"""
Historical OHLCV Data Loader for Backtesting

Uses ccxt with the Binance USDT-M futures public endpoint.
No API keys are required for historical OHLCV data.

Features:
- Paginated fetch for long history windows
- Automatic in-place caching to CSV (avoids re-fetching on repeated runs)
- Validates and deduplicates by timestamp
- Returns standard pandas DataFrame with a UTC-aware DatetimeIndex
"""

from __future__ import annotations

import os
import time
import pandas as pd
import ccxt
from logs.logger import get_logger

logger = get_logger("backtesting.data_loader")

_COLUMNS = ["open", "high", "low", "close", "volume"]

# Maps (symbol, timeframe) → minutes per candle — used for sanity checks
_TIMEFRAME_MINUTES: dict[str, int] = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15,
    "30m": 30, "1h": 60, "4h": 240, "1d": 1440,
}


def _make_exchange() -> ccxt.binanceusdm:
    """Return a public Binance USDT-M futures exchange instance (no auth needed)."""
    return ccxt.binanceusdm({
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
    })


def _ohlcv_to_df(raw: list[list]) -> pd.DataFrame:
    """Convert raw ccxt OHLCV rows → clean DataFrame with UTC DatetimeIndex."""
    df = pd.DataFrame(raw, columns=["timestamp"] + _COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp")
    df = df[~df.index.duplicated(keep="last")]
    df = df.sort_index()
    return df


def fetch_ohlcv_paginated(
    symbol: str,
    timeframe: str,
    total_candles: int,
    cache_dir: str | None = None,
    use_cache: bool = True,
) -> pd.DataFrame:
    """
    Fetch `total_candles` candles for `symbol` / `timeframe` from Binance
    USDT-M futures. Uses paginated requests (max 1 500 per call) and
    optionally caches the result to `<cache_dir>/<symbol>_<timeframe>.csv`.

    Args:
        symbol:         e.g. "BTCUSDT"
        timeframe:      e.g. "15m", "1h"
        total_candles:  number of candles to fetch (older → newer)
        cache_dir:      directory for CSV cache files; None disables caching
        use_cache:      if True, try loading from CSV before fetching

    Returns:
        pd.DataFrame with UTC DatetimeIndex and columns [open, high, low, close, volume]
    """
    safe_symbol = symbol.replace("/", "_")
    cache_path = (
        os.path.join(cache_dir, f"{safe_symbol}_{timeframe}.csv")
        if cache_dir
        else None
    )

    if use_cache and cache_path and os.path.exists(cache_path):
        try:
            df = _load_csv_cache(cache_path)
            if len(df) >= max(50, total_candles // 2):
                logger.info(
                    "Cache hit: %s/%s → %s (%d rows)",
                    symbol, timeframe, cache_path, len(df),
                )
                return df.iloc[-total_candles:] if len(df) > total_candles else df
        except Exception as exc:
            logger.warning("Cache load failed (%s); fetching fresh data. %s", cache_path, exc)

    logger.info("Fetching %d candles for %s/%s from Binance...", total_candles, symbol, timeframe)
    exchange = _make_exchange()
    exchange.load_markets()

    per_page = 1500  # Binance OHLCV max per request
    all_rows: list[list] = []

    # Start from far enough back
    tf_min = _TIMEFRAME_MINUTES.get(timeframe, 15)
    ms_per_candle = tf_min * 60 * 1000
    start_ms = exchange.milliseconds() - total_candles * ms_per_candle - ms_per_candle

    since = start_ms

    while len(all_rows) < total_candles:
        to_fetch = min(per_page, total_candles - len(all_rows))
        try:
            batch = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=to_fetch)
        except ccxt.NetworkError as exc:
            logger.error("Network error fetching %s/%s: %s", symbol, timeframe, exc)
            time.sleep(5)
            continue
        except ccxt.ExchangeError as exc:
            logger.error("Exchange error fetching %s/%s: %s", symbol, timeframe, exc)
            break

        if not batch:
            break

        all_rows.extend(batch)
        last_ts = batch[-1][0]
        since = last_ts + ms_per_candle  # next page starts after last fetched candle

        if len(batch) < to_fetch:
            break  # no more data available

        time.sleep(exchange.rateLimit / 1000)

    if not all_rows:
        raise RuntimeError(
            f"No OHLCV data returned for {symbol}/{timeframe}. "
            "Check the symbol name and network connectivity."
        )

    df = _ohlcv_to_df(all_rows)
    df = df.iloc[-total_candles:]  # keep exactly what was requested

    if use_cache and cache_path:
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            df.to_csv(cache_path)
            logger.info("Cached %d rows → %s", len(df), cache_path)
        except Exception as exc:
            logger.warning("Cache write failed: %s", exc)

    logger.info(
        "Loaded %d candles for %s/%s  [%s → %s]",
        len(df), symbol, timeframe,
        df.index[0].isoformat(), df.index[-1].isoformat(),
    )
    return df


def _load_csv_cache(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    df = df[_COLUMNS].copy()
    df = df.sort_index()
    return df


def load_ohlcv_from_csv(path: str) -> pd.DataFrame:
    """
    Load OHLCV data from a CSV file with a timestamp column or index.
    Useful for providing your own data source.
    """
    df = _load_csv_cache(path)
    logger.info("Loaded %d rows from %s", len(df), path)
    return df
