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
    Place a demo market order on the Binance Futures testnet.

    For futures, 'buy' opens a long position, 'sell' opens a short position.
    Both work correctly on the Futures testnet — this is why we use futures
    instead of spot.

    Stop loss and take profit are managed app-side (stored in DB, checked
    each monitoring loop). Exchange-native bracket orders would require
    additional ccxt calls which are not stable across all testnet versions.

    Returns the stored Trade on success, None on any failure.
    """
    if store.has_open_trade_for_symbol(params.symbol):
        logger.info(
            "%s: Order skipped — open trade already exists for this symbol.",
            params.symbol,
        )
        return None

    side = "buy" if params.direction == "long" else "sell"

    logger.info(
        "ORDER: %s %s | qty=%.6f | ~entry=%.4f | SL=%.4f | TP=%.4f",
        side.upper(), params.symbol,
        params.quantity, params.entry_price,
        params.stop_loss, params.take_profit,
    )

    try:
        order = exchange.create_market_order(
            symbol=params.symbol,
            side=side,
            amount=params.quantity,
            params={"positionSide": "LONG" if params.direction == "long" else "SHORT"},
        )
    except ccxt.InsufficientFunds as exc:
        logger.error(
            "%s: Insufficient funds. qty=%.6f, ~cost=%.2f USDT. %s",
            params.symbol, params.quantity,
            params.quantity * params.entry_price, exc,
        )
        return None
    except ccxt.InvalidOrder as exc:
        logger.error("%s: Invalid order parameters: %s", params.symbol, exc)
        return None
    except ccxt.ExchangeError as exc:
        logger.error("%s: Exchange error placing order: %s", params.symbol, exc)
        return None
    except ccxt.NetworkError as exc:
        logger.error("%s: Network error placing order: %s", params.symbol, exc)
        return None

    # Extract actual fill price (fallback to params if not available)
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
        risk_distance=params.risk_distance,
        atr=params.atr,
        candle_timestamp="",  # populated from setup at call site in app.py
        trend_1h="",
        opened_at=opened_at,
        status="open",
    )

    store.save_open_trade(trade)

    logger.info(
        "TRADE OPENED | id=%s | %s %s | fill=%.4f | SL=%.4f | TP=%.4f | qty=%.6f | "
        "risk=%.2f USDT | reward=%.2f USDT",
        trade.id, trade.direction.upper(), trade.symbol,
        trade.entry_price, trade.stop_loss, trade.take_profit, trade.quantity,
        trade.risk_amount_usdt, trade.reward_amount_usdt,
    )

    return trade


def execute_demo_order_from_setup(
    exchange: ccxt.binance,
    params: TradeParameters,
    store: TradeStore,
    candle_timestamp: str,
    trend_1h: str,
) -> Trade | None:
    """
    Wrapper that enriches the trade with setup context (candle timestamp, trend)
    before saving. This is what app.py should call.
    """
    trade = execute_demo_order(exchange, params, store)
    if trade is not None:
        trade.candle_timestamp = candle_timestamp
        trade.trend_1h = trend_1h
        store.save_open_trade(trade)
    return trade
