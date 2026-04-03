import uuid
from datetime import datetime, timezone
import ccxt
from strategy.signal import SignalEvaluation
from storage.trade_store import Trade, TradeStore
from logs.logger import get_logger

logger = get_logger("execution.order_executor")


def execute_from_signal(
    exchange: ccxt.binance,
    signal: SignalEvaluation,
    store: TradeStore,
) -> Trade | None:
    """
    Execute a demo market order from an approved SignalEvaluation.

    The signal must have approved=True and all execution fields populated
    (entry_price, stop_loss, take_profit, quantity, etc.).

    Returns the stored Trade on success, None on any failure.
    """
    if not signal.approved:
        logger.error(
            "%s: execute_from_signal called with non-approved signal. "
            "Code: %s. This is a programming error.",
            signal.symbol, signal.rejection_code,
        )
        return None

    if store.has_open_trade_for_symbol(signal.symbol):
        logger.info(
            "%s: Order skipped — open trade already exists for this symbol.",
            signal.symbol,
        )
        return None

    side = "buy" if signal.direction == "long" else "sell"

    logger.info(
        "ORDER | %s %s | qty=%.6f | ~entry=%.4f | SL=%.4f | TP=%.4f | "
        "regime=%s(%.2f) | vol_ratio=%.2f | body/range=%.2f | "
        "dist_ema_atr=%.2f | candle=%s",
        side.upper(), signal.symbol,
        signal.quantity, signal.entry_price,
        signal.stop_loss, signal.take_profit,
        signal.regime_label, signal.regime_score or 0,
        signal.volume_ratio or 0,
        signal.body_to_range_ratio or 0,
        signal.distance_from_ema_atr or 0,
        signal.candle_timestamp,
    )

    try:
        order = exchange.create_market_order(
            symbol=signal.symbol,
            side=side,
            amount=signal.quantity,
            params={"positionSide": "LONG" if signal.direction == "long" else "SHORT"},
        )
    except ccxt.InsufficientFunds as exc:
        logger.error(
            "%s: Insufficient funds. qty=%.6f ~cost=%.2f USDT. %s",
            signal.symbol, signal.quantity,
            signal.quantity * signal.entry_price, exc,
        )
        return None
    except ccxt.InvalidOrder as exc:
        logger.error("%s: Invalid order parameters: %s", signal.symbol, exc)
        return None
    except ccxt.ExchangeError as exc:
        logger.error("%s: Exchange error placing order: %s", signal.symbol, exc)
        return None
    except ccxt.NetworkError as exc:
        logger.error("%s: Network error placing order: %s", signal.symbol, exc)
        return None

    filled_price = float(
        order.get("average") or order.get("price") or signal.entry_price
    )
    order_id = str(order.get("id", uuid.uuid4().hex))
    trade_id = f"{signal.symbol}_{order_id}"
    opened_at = datetime.now(timezone.utc).isoformat()

    trade = Trade(
        id=trade_id,
        symbol=signal.symbol,
        direction=signal.direction,
        entry_price=filled_price,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        quantity=signal.quantity,
        risk_amount_usdt=signal.risk_amount_usdt,
        reward_amount_usdt=signal.reward_amount_usdt,
        risk_distance=signal.risk_distance,
        atr=signal.atr,
        candle_timestamp=signal.candle_timestamp,
        trend_1h=signal.trend_state,
        regime_label=signal.regime_label,
        regime_score=signal.regime_score or 0.0,
        opened_at=opened_at,
        status="open",
    )

    store.save_open_trade(trade)

    logger.info(
        "TRADE OPENED | id=%s | %s %s | fill=%.4f | SL=%.4f | TP=%.4f | "
        "qty=%.6f | risk=%.2f USDT | reward=%.2f USDT | "
        "regime=%s(%.2f) | trend=%s | candle=%s",
        trade.id, trade.direction.upper(), trade.symbol,
        trade.entry_price, trade.stop_loss, trade.take_profit,
        trade.quantity, trade.risk_amount_usdt, trade.reward_amount_usdt,
        trade.regime_label, trade.regime_score,
        trade.trend_1h, trade.candle_timestamp,
    )

    return trade
