import ccxt
from config.settings import settings
from logs.logger import get_logger

logger = get_logger("exchange.connector")


def create_exchange() -> ccxt.binance:
    """
    Create and return a ccxt Binance exchange instance configured for the testnet.
    Raises RuntimeError if connection or credential check fails.
    """
    exchange = ccxt.binance({
        "apiKey": settings.BINANCE_API_KEY,
        "secret": settings.BINANCE_API_SECRET,
        "options": {
            "defaultType": "spot",
        },
        "enableRateLimit": True,
    })

    exchange.set_sandbox_mode(True)

    logger.info("Connecting to Binance testnet...")

    try:
        exchange.load_markets()
        logger.info(
            "Successfully connected to Binance testnet. %d markets loaded.",
            len(exchange.markets),
        )
    except ccxt.AuthenticationError as exc:
        raise RuntimeError(
            "Binance testnet authentication failed. "
            "Ensure BINANCE_API_KEY and BINANCE_API_SECRET are valid testnet credentials. "
            f"Detail: {exc}"
        ) from exc
    except ccxt.NetworkError as exc:
        raise RuntimeError(
            f"Network error connecting to Binance testnet: {exc}"
        ) from exc

    return exchange


def fetch_balance(exchange: ccxt.binance) -> float:
    """
    Fetch USDT balance from the testnet account.
    Returns the free USDT balance as a float.
    """
    try:
        balance = exchange.fetch_balance()
        usdt_free = float(balance.get("USDT", {}).get("free", 0.0))
        logger.info("Account USDT balance: %.2f", usdt_free)
        return usdt_free
    except ccxt.AuthenticationError as exc:
        raise RuntimeError(
            f"Authentication error fetching balance: {exc}. "
            "Check your testnet API credentials."
        ) from exc
    except ccxt.ExchangeError as exc:
        raise RuntimeError(f"Exchange error fetching balance: {exc}") from exc
