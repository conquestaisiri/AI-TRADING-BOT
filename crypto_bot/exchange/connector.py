import ccxt
from config.settings import settings
from logs.logger import get_logger

logger = get_logger("exchange.connector")


def create_exchange() -> ccxt.binance:
    """
    Create and return a ccxt Binance exchange instance configured for the
    FUTURES testnet. Futures testnet supports both long and short positions
    correctly, unlike spot which cannot short.

    Credentials: https://testnet.binancefuture.com
    Raises RuntimeError on authentication or network failure.
    """
    exchange = ccxt.binance({
        "apiKey": settings.BINANCE_API_KEY,
        "secret": settings.BINANCE_API_SECRET,
        "options": {
            "defaultType": settings.EXCHANGE_TYPE,  # "future"
        },
        "enableRateLimit": True,
    })

    exchange.set_sandbox_mode(True)

    logger.info("Connecting to Binance Futures testnet...")
    logger.info(
        "Exchange type: %s | Sandbox: enabled",
        settings.EXCHANGE_TYPE,
    )

    try:
        exchange.load_markets()
        logger.info(
            "Connected to Binance Futures testnet. %d markets loaded.",
            len(exchange.markets),
        )
    except ccxt.AuthenticationError as exc:
        raise RuntimeError(
            "Binance Futures testnet authentication failed.\n"
            "Make sure you are using FUTURES testnet credentials from "
            "https://testnet.binancefuture.com — NOT the spot testnet.\n"
            f"Detail: {exc}"
        ) from exc
    except ccxt.NetworkError as exc:
        raise RuntimeError(
            f"Network error connecting to Binance Futures testnet: {exc}"
        ) from exc

    return exchange


def fetch_usdt_balance(exchange: ccxt.binance) -> float:
    """
    Fetch the free USDT balance from the futures testnet account.
    Returns 0.0 on failure (caller handles fallback).
    """
    try:
        balance = exchange.fetch_balance({"type": "future"})
        usdt = balance.get("USDT", {})
        free = float(usdt.get("free", 0.0))
        total = float(usdt.get("total", 0.0))
        logger.info("Account balance — Free USDT: %.2f | Total USDT: %.2f", free, total)
        return free
    except ccxt.AuthenticationError as exc:
        logger.error("Authentication error fetching balance: %s", exc)
        return 0.0
    except ccxt.ExchangeError as exc:
        logger.error("Exchange error fetching balance: %s", exc)
        return 0.0
