import os
from dotenv import load_dotenv

load_dotenv()


def _require_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(
            f"Required environment variable '{key}' is not set. "
            f"Copy .env.example to .env and fill in your Binance testnet credentials."
        )
    return value


def _float_env(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        raise RuntimeError(f"Environment variable '{key}' must be a float, got: {raw!r}")


def _int_env(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"Environment variable '{key}' must be an integer, got: {raw!r}")


class Settings:
    BINANCE_API_KEY: str = _require_env("BINANCE_API_KEY")
    BINANCE_API_SECRET: str = _require_env("BINANCE_API_SECRET")

    SYMBOLS: list[str] = [
        s.strip() for s in os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT").split(",") if s.strip()
    ]

    RISK_PERCENT: float = _float_env("RISK_PERCENT", 1.0)
    REWARD_TO_RISK: float = _float_env("REWARD_TO_RISK", 2.0)
    STARTING_DEMO_BALANCE_USDT: float = _float_env("STARTING_DEMO_BALANCE_USDT", 10000.0)
    LOOP_INTERVAL_SECONDS: int = _int_env("LOOP_INTERVAL_SECONDS", 900)

    TIMEFRAME_TREND: str = "1h"
    TIMEFRAME_ENTRY: str = "15m"

    OHLCV_LIMIT: int = 200

    EMA_FAST: int = 20
    EMA_SLOW: int = 50
    RSI_PERIOD: int = 14
    ATR_PERIOD: int = 14
    VOLUME_AVG_PERIOD: int = 20
    SWING_LOOKBACK: int = 20


settings = Settings()
