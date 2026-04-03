from datetime import datetime, timezone
import ccxt
from storage.trade_store import Trade, TradeStore
from logs.logger import get_logger

logger = get_logger("monitoring.position_monitor")


def get_current_price(exchange: ccxt.binance, symbol: str) -> float | None:
    """
    Fetch the latest mark/last price for a futures symbol.
    Returns None on any exchange or network failure.
    """
    try:
        ticker = exchange.fetch_ticker(symbol)

        # For futures, prefer mark price if available, fall back to last
        price = ticker.get("mark") or ticker.get("last")
        if price is None:
            logger.warning("%s: Ticker returned no usable price. ticker=%s", symbol, ticker)
            return None

        return float(price)
    except ccxt.BadSymbol:
        logger.error("%s: Symbol not found on exchange. Cannot fetch price.", symbol)
        return None
    except ccxt.ExchangeError as exc:
        logger.error("%s: Exchange error fetching price: %s", symbol, exc)
        return None
    except ccxt.NetworkError as exc:
        logger.warning("%s: Network error fetching price (will retry next cycle): %s", symbol, exc)
        return None


def _compute_pnl(trade: Trade, close_price: float) -> float:
    """
    Compute realised PnL in USDT for a futures trade.
    Long:  (close - entry) * qty
    Short: (entry - close) * qty
    """
    if trade.direction == "long":
        return round((close_price - trade.entry_price) * trade.quantity, 4)
    else:
        return round((trade.entry_price - close_price) * trade.quantity, 4)


def monitor_open_trades(
    exchange: ccxt.binance,
    store: TradeStore,
) -> list[Trade]:
    """
    Check all open trades against current prices.
    Closes any trade that has hit its stop loss or take profit level.

    Returns the list of trades closed during this cycle.

    Note: SL/TP are managed app-side. The exchange's own protective orders
    are not placed (testnet bracket order reliability varies). This means
    the bot must run continuously to protect open positions — document this
    clearly to the user.
    """
    open_trades = store.load_open_trades()

    if not open_trades:
        logger.info("No open trades to monitor.")
        return []

    logger.info("Monitoring %d open trade(s)...", len(open_trades))
    closed_this_cycle: list[Trade] = []

    for trade in open_trades:
        current_price = get_current_price(exchange, trade.symbol)

        if current_price is None:
            logger.warning(
                "%s: Cannot get current price — skipping this trade for now.",
                trade.symbol,
            )
            continue

        logger.debug(
            "%s %s | price=%.4f | SL=%.4f | TP=%.4f | entry=%.4f | qty=%.6f",
            trade.symbol, trade.direction.upper(),
            current_price, trade.stop_loss, trade.take_profit,
            trade.entry_price, trade.quantity,
        )

        hit_sl = False
        hit_tp = False

        if trade.direction == "long":
            hit_sl = current_price <= trade.stop_loss
            hit_tp = current_price >= trade.take_profit
        else:
            hit_sl = current_price >= trade.stop_loss
            hit_tp = current_price <= trade.take_profit

        if hit_sl or hit_tp:
            close_price = trade.stop_loss if hit_sl else trade.take_profit
            pnl = _compute_pnl(trade, close_price)
            reason = "closed_sl" if hit_sl else "closed_tp"

            trade.status = reason
            trade.close_price = close_price
            trade.pnl_usdt = pnl
            trade.closed_at = datetime.now(timezone.utc).isoformat()

            store.close_trade(trade)
            closed_this_cycle.append(trade)

            logger.info(
                "%s %s: %s | price=%.4f | %s=%.4f | entry=%.4f | "
                "PnL=%.2f USDT | qty=%.6f",
                trade.symbol, trade.direction.upper(),
                "STOP HIT" if hit_sl else "TARGET HIT",
                current_price,
                "SL" if hit_sl else "TP",
                close_price,
                trade.entry_price,
                pnl,
                trade.quantity,
            )
        else:
            # Still open — log unrealised PnL for transparency
            unrealised_pnl = _compute_pnl(trade, current_price)
            logger.info(
                "%s %s: WATCHING | price=%.4f | SL=%.4f | TP=%.4f | "
                "unrealised_pnl=%.2f USDT",
                trade.symbol, trade.direction.upper(),
                current_price, trade.stop_loss, trade.take_profit,
                unrealised_pnl,
            )

    return closed_this_cycle
