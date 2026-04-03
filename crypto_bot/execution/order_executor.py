import uuid
from datetime import datetime, timezone
import ccxt
from risk.calculator import TradeParameters
from storage.trade_store import Trade, TradeStore
from logs.logger import get_logger

logger = get_logger("execution.order_executor")


def execute_demo_order(
    exchange: ccxt.binance,
    params: TradeParameters,
    store: TradeStore,
) -> Trade | None:
    """
    Place a demo market order on the Binance testnet.
    Stores the resulting trade including stop loss and take profit levels.

    Returns the Trade object if successful, None on failure.
    """
    if store.has_open_trade_for_symbol(params.symbol):
        logger.info(
            "%s: Skipping — an open trade already exists for this symbol.",
            params.symbol,
        )
        return None

    side = "buy" if params.direction == "long" else "sell"

    logger.info(
        "Placing %s market order on testnet: %s %s qty=%.6f at ~%.4f",
        side.upper(), params.symbol, params.direction, params.quantity, params.entry_price,
    )

    try:
        order = exchange.create_market_order(
            symbol=params.symbol,
            side=side,
            amount=params.quantity,
        )
    except ccxt.InsufficientFunds as exc:
        logger.error(
            "%s: Insufficient testnet funds for order. qty=%.6f, ~cost=%.2f USDT. Detail: %s",
            params.symbol, params.quantity, params.quantity * params.entry_price, exc,
        )
        return None
    except ccxt.ExchangeError as exc:
        logger.error("%s: Exchange error placing order: %s", params.symbol, exc)
        return None
    except ccxt.NetworkError as exc:
        logger.error("%s: Network error placing order: %s", params.symbol, exc)
        return None

    filled_price = float(order.get("average") or order.get("price") or params.entry_price)
    order_id = str(order.get("id", uuid.uuid4().hex))

    trade_id = f"{params.symbol}_{order_id}"
    opened_at = datetime.now(timezone.utc).isoformat()

    trade = Trade(
        id=trade_id,
        symbol=params.symbol,
        direction=params.direction,
        entry_price=filled_price,
        stop_loss=params.stop_loss,
        take_profit=params.take_profit,
        quantity=params.quantity,
        risk_amount_usdt=params.risk_amount_usdt,
        reward_amount_usdt=params.reward_amount_usdt,
        opened_at=opened_at,
        status="open",
    )

    store.save_open_trade(trade)

    logger.info(
        "Trade opened: %s | %s | Entry=%.4f | SL=%.4f | TP=%.4f | Qty=%.6f",
        trade.id, trade.direction, trade.entry_price,
        trade.stop_loss, trade.take_profit, trade.quantity,
    )

    return trade
