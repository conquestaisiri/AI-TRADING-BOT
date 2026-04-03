import os
from dotenv import load_dotenv

load_dotenv()


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
    Call settings.validate() at startup before any module runs.
    """

    def __init__(self):
        # ── Exchange credentials ─────────────────────────────────────────────
        self.BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
        self.BINANCE_API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")
        self.EXCHANGE_TYPE: str = os.getenv("EXCHANGE_TYPE", "future")

        # ── Symbols ──────────────────────────────────────────────────────────
        raw_symbols = os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT")
        self.SYMBOLS: list[str] = [s.strip() for s in raw_symbols.split(",") if s.strip()]

        # ── Core risk ────────────────────────────────────────────────────────
        self.RISK_PERCENT: float = _float_env("RISK_PERCENT", 1.0)
        self.REWARD_TO_RISK: float = _float_env("REWARD_TO_RISK", 2.0)
        self.ATR_STOP_MULTIPLIER: float = _float_env("ATR_STOP_MULTIPLIER", 1.5)
        self.STARTING_DEMO_BALANCE_USDT: float = _float_env("STARTING_DEMO_BALANCE_USDT", 10000.0)

        # ── Indicator periods ─────────────────────────────────────────────────
        self.OHLCV_LIMIT: int = _int_env("OHLCV_LIMIT", 300)
        self.EMA_FAST: int = 20
        self.EMA_SLOW: int = 50
        self.RSI_PERIOD: int = 14
        self.ATR_PERIOD: int = 14
        self.ATR_MA_PERIOD: int = 14   # ATR moving average for regime expansion check
        self.EMA_SLOPE_PERIOD: int = 5  # candles over which EMA slope is measured
        self.VOLUME_AVG_PERIOD: int = 20
        self.SWING_LOOKBACK: int = 20

        # ── Timeframes ───────────────────────────────────────────────────────
        self.TIMEFRAME_TREND: str = "1h"
        self.TIMEFRAME_ENTRY: str = "15m"
        self.ENTRY_CANDLE_MINUTES: int = 15  # used for cooldown candle counting

        # ── Volume filter ────────────────────────────────────────────────────
        # Volume must be >= VOLUME_RATIO_THRESHOLD × avg_volume
        self.VOLUME_RATIO_THRESHOLD: float = _float_env("VOLUME_RATIO_THRESHOLD", 1.5)

        # ── Market regime filter ──────────────────────────────────────────────
        # Minimum regime score (0-1) required to allow a trade.
        # Score reflects how clearly trending the market is.
        self.REGIME_MIN_TREND_SCORE: float = _float_env("REGIME_MIN_TREND_SCORE", 0.50)

        # ── Overextension filter ──────────────────────────────────────────────
        # Reject if candle body > MAX_BODY_ATR_RATIO × ATR (entry candle too large)
        self.MAX_BODY_ATR_RATIO: float = _float_env("MAX_BODY_ATR_RATIO", 2.0)
        # Reject if price distance from EMA20 > MAX_DISTANCE_FROM_EMA_ATR_RATIO × ATR
        self.MAX_DISTANCE_FROM_EMA_ATR_RATIO: float = _float_env("MAX_DISTANCE_FROM_EMA_ATR_RATIO", 3.0)

        # ── Breakout quality filter ───────────────────────────────────────────
        # Close must exceed the breakout level by at least BREAKOUT_CLOSE_BUFFER_RATIO × ATR
        self.BREAKOUT_CLOSE_BUFFER_RATIO: float = _float_env("BREAKOUT_CLOSE_BUFFER_RATIO", 0.10)
        # Candle body must be >= MIN_BODY_TO_RANGE_RATIO × candle range (rejects wick-heavy rejections)
        self.MIN_BODY_TO_RANGE_RATIO: float = _float_env("MIN_BODY_TO_RANGE_RATIO", 0.40)

        # ── Legacy RSI / ATR filters (kept for backwards compatibility) ───────
        self.RSI_OVERBOUGHT: float = _float_env("RSI_OVERBOUGHT", 72.0)
        self.RSI_OVERSOLD: float = _float_env("RSI_OVERSOLD", 28.0)
        self.EMA_MIN_SPREAD_PCT: float = _float_env("EMA_MIN_SPREAD_PCT", 0.10)
        self.ATR_MIN_PCT: float = _float_env("ATR_MIN_PCT", 0.05)

        # ── Cooldown after closed trades ──────────────────────────────────────
        # After a losing trade, block new trades on that symbol for N completed 15m candles
        self.LOSS_COOLDOWN_CANDLES: int = _int_env("LOSS_COOLDOWN_CANDLES", 3)
        # After a winning trade, block new trades for N completed 15m candles
        self.WIN_COOLDOWN_CANDLES: int = _int_env("WIN_COOLDOWN_CANDLES", 1)

        # ── Trade frequency limiter ───────────────────────────────────────────
        # Max trades per symbol within a rolling time window
        self.MAX_TRADES_PER_WINDOW: int = _int_env("MAX_TRADES_PER_WINDOW", 3)
        # Rolling window duration in minutes
        self.TRADE_WINDOW_MINUTES: int = _int_env("TRADE_WINDOW_MINUTES", 480)
        # Minimum minutes between consecutive entries on the same symbol
        self.MIN_ENTRY_GAP_MINUTES: int = _int_env("MIN_ENTRY_GAP_MINUTES", 60)

        # ── Loop timing ───────────────────────────────────────────────────────
        self.LOOP_INTERVAL_SECONDS: int = _int_env("LOOP_INTERVAL_SECONDS", 900)

        # ── Backtesting ───────────────────────────────────────────────────────
        # Initial balance for backtest simulation (USDT)
        self.BACKTEST_INITIAL_BALANCE: float = _float_env("BACKTEST_INITIAL_BALANCE", 10_000.0)
        # Fee per leg as a decimal (0.0004 = 0.04%, Binance USDT-M taker rate)
        self.BACKTEST_FEE_RATE: float = _float_env("BACKTEST_FEE_RATE", 0.0004)
        # Slippage per leg as a decimal (0.0002 = 0.02%, conservative estimate)
        self.BACKTEST_SLIPPAGE_RATE: float = _float_env("BACKTEST_SLIPPAGE_RATE", 0.0002)
        # Entry mode: only "next_open" is supported — enters at the open of the
        # candle following signal confirmation.
        self.BACKTEST_ENTRY_MODE: str = os.getenv("BACKTEST_ENTRY_MODE", "next_open")
        # Total 15m candles to fetch per symbol for the backtest window.
        # 2000 ≈ 21 days; 6000 ≈ 63 days; 10000 ≈ 104 days.
        self.BACKTEST_CANDLE_LIMIT: int = _int_env("BACKTEST_CANDLE_LIMIT", 2000)
        # Symbols to backtest (comma-separated); defaults to live SYMBOLS setting
        raw_bt_syms = os.getenv("BACKTEST_SYMBOLS", "")
        self.BACKTEST_SYMBOLS: list[str] = (
            [s.strip() for s in raw_bt_syms.split(",") if s.strip()]
            if raw_bt_syms.strip()
            else self.SYMBOLS
        )
        # Directory where backtest result files are written
        self.BACKTEST_EXPORT_DIR: str = os.getenv("BACKTEST_EXPORT_DIR", "backtest_results")

    def validate(self) -> None:
        """
        Call once at startup. Raises RuntimeError with a list of all problems
        if any required setting is missing or invalid.
        """
        errors: list[str] = []

        if not self.BINANCE_API_KEY:
            errors.append(
                "BINANCE_API_KEY not set. "
                "Get credentials from https://testnet.binancefuture.com"
            )
        if not self.BINANCE_API_SECRET:
            errors.append(
                "BINANCE_API_SECRET not set. "
                "Get credentials from https://testnet.binancefuture.com"
            )
        if not self.SYMBOLS:
            errors.append("SYMBOLS must be a non-empty comma-separated list, e.g. BTCUSDT,ETHUSDT")
        if not (0 < self.RISK_PERCENT <= 10):
            errors.append(f"RISK_PERCENT must be between 0 and 10 (got {self.RISK_PERCENT})")
        if self.REWARD_TO_RISK < 1.0:
            errors.append(f"REWARD_TO_RISK must be >= 1.0 (got {self.REWARD_TO_RISK})")
        if not (0 < self.REGIME_MIN_TREND_SCORE <= 1.0):
            errors.append(f"REGIME_MIN_TREND_SCORE must be between 0 and 1 (got {self.REGIME_MIN_TREND_SCORE})")
        if self.VOLUME_RATIO_THRESHOLD < 1.0:
            errors.append(f"VOLUME_RATIO_THRESHOLD must be >= 1.0 (got {self.VOLUME_RATIO_THRESHOLD})")
        if self.MAX_BODY_ATR_RATIO <= 0:
            errors.append(f"MAX_BODY_ATR_RATIO must be > 0 (got {self.MAX_BODY_ATR_RATIO})")
        if self.MAX_TRADES_PER_WINDOW < 1:
            errors.append(f"MAX_TRADES_PER_WINDOW must be >= 1 (got {self.MAX_TRADES_PER_WINDOW})")
        if self.TRADE_WINDOW_MINUTES < 1:
            errors.append(f"TRADE_WINDOW_MINUTES must be >= 1 (got {self.TRADE_WINDOW_MINUTES})")

        if errors:
            msg = "\n".join(f"  - {e}" for e in errors)
            raise RuntimeError(
                f"[Config] Bot cannot start. Fix these issues in your .env file:\n{msg}"
            )


settings = Settings()
