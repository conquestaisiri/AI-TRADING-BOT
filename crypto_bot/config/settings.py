import os
from dotenv import load_dotenv

load_dotenv()


class _MissingEnv:
    """Sentinel to detect unset required env vars at startup validation."""
    def __init__(self, key: str):
        self.key = key

    def __repr__(self):
        return f"<MISSING:{self.key}>"


def _read_env(key: str, default=None):
    val = os.getenv(key)
    if val is None:
        if default is _MissingEnv:
            return _MissingEnv(key)
        return default
    return val


def _float_env(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        raise RuntimeError(f"[Config] '{key}' must be a float, got: {raw!r}")


def _int_env(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"[Config] '{key}' must be an integer, got: {raw!r}")


class Settings:
    """
    All bot settings loaded from environment variables.
    Call settings.validate() at startup to get a clear error before any module runs.
    """

    def __init__(self):
        # Exchange credentials — read now, validated at startup
        self.BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
        self.BINANCE_API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")

        # Trading pairs
        raw_symbols = os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT")
        self.SYMBOLS: list[str] = [s.strip() for s in raw_symbols.split(",") if s.strip()]

        # Risk settings
        self.RISK_PERCENT: float = _float_env("RISK_PERCENT", 1.0)
        self.REWARD_TO_RISK: float = _float_env("REWARD_TO_RISK", 2.0)
        self.ATR_STOP_MULTIPLIER: float = _float_env("ATR_STOP_MULTIPLIER", 1.5)
        self.STARTING_DEMO_BALANCE_USDT: float = _float_env("STARTING_DEMO_BALANCE_USDT", 10000.0)

        # Strategy filters
        # RSI: don't chase longs above this or shorts below this
        self.RSI_OVERBOUGHT: float = _float_env("RSI_OVERBOUGHT", 72.0)
        self.RSI_OVERSOLD: float = _float_env("RSI_OVERSOLD", 28.0)
        # EMA spread: minimum % gap between EMA20 and EMA50 to confirm real trend
        self.EMA_MIN_SPREAD_PCT: float = _float_env("EMA_MIN_SPREAD_PCT", 0.1)
        # ATR minimum as % of price — reject if ATR is suspiciously low (choppy market)
        self.ATR_MIN_PCT: float = _float_env("ATR_MIN_PCT", 0.05)
        # Max consecutive losses before cooldown kicks in
        self.LOSS_COOLDOWN_COUNT: int = _int_env("LOSS_COOLDOWN_COUNT", 2)
        # How many loops to wait after cooldown trigger
        self.LOSS_COOLDOWN_LOOPS: int = _int_env("LOSS_COOLDOWN_LOOPS", 2)

        # Timeframes
        self.TIMEFRAME_TREND: str = "1h"
        self.TIMEFRAME_ENTRY: str = "15m"

        # Indicator periods
        self.OHLCV_LIMIT: int = _int_env("OHLCV_LIMIT", 300)
        self.EMA_FAST: int = 20
        self.EMA_SLOW: int = 50
        self.RSI_PERIOD: int = 14
        self.ATR_PERIOD: int = 14
        self.VOLUME_AVG_PERIOD: int = 20
        self.SWING_LOOKBACK: int = 20

        # Loop timing
        self.LOOP_INTERVAL_SECONDS: int = _int_env("LOOP_INTERVAL_SECONDS", 900)

        # Exchange mode: "futures" is required for proper long/short support
        # Binance Futures Testnet: https://testnet.binancefuture.com
        self.EXCHANGE_TYPE: str = os.getenv("EXCHANGE_TYPE", "future")

    def validate(self) -> None:
        """
        Call this once at bot startup. Raises RuntimeError with a clear message
        if any required setting is missing or invalid.
        """
        errors: list[str] = []

        if not self.BINANCE_API_KEY:
            errors.append(
                "BINANCE_API_KEY is not set. "
                "Get it from https://testnet.binancefuture.com (Futures Testnet)."
            )
        if not self.BINANCE_API_SECRET:
            errors.append(
                "BINANCE_API_SECRET is not set. "
                "Get it from https://testnet.binancefuture.com (Futures Testnet)."
            )

        if not self.SYMBOLS:
            errors.append("SYMBOLS must be a non-empty comma-separated list, e.g. BTCUSDT,ETHUSDT")

        if not (0 < self.RISK_PERCENT <= 10):
            errors.append(f"RISK_PERCENT must be between 0 and 10, got {self.RISK_PERCENT}")

        if self.REWARD_TO_RISK < 1.0:
            errors.append(f"REWARD_TO_RISK must be >= 1.0, got {self.REWARD_TO_RISK}")

        if errors:
            msg = "\n".join(f"  - {e}" for e in errors)
            raise RuntimeError(
                f"[Config] Bot cannot start. Fix these issues in your .env file:\n{msg}"
            )


settings = Settings()
