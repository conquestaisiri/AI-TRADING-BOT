from datetime import datetime, timezone
import ccxt
import pandas as pd
from storage.trade_store import Trade, TradeStore
from data.market_data import fetch_ohlcv
from config.settings import settings
from logs.logger import get_logger

logger = get_logger("monitoring.position_monitor")


def get_current_price(exchange: ccxt.binance, symbol: str) -> float | None:
    """
    Fetch the latest close price for a symbol using the entry timeframe.
    Returns None on failure.
    """
    try:
        ticker = exchange.fetch_ticker(symbol)
        price = float(ticker["last"])
        return price
    except ccxt.ExchangeError as exc:
        logger.error("Failed to fetch ticker for %s: %s", symbol, exc)
        return None
    except ccxt.NetworkError as exc:
        logger.error("Network error fetching ticker for %s: %s", symbol, exc)
        return None


def monitor_open_trades(exchange: ccxt.binance, store: TradeStore) -> list[Trade]:
    """
    Check all open trades against current prices.
    Close trades that have hit stop loss or take profit.
    Returns the list of trades that were closed this cycle.
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
            logger.warning("%s: Could not get price, skipping monitor check.", trade.symbol)
            continue

        closed = False

        if trade.direction == "long":
            if current_price <= trade.stop_loss:
                pnl = (trade.stop_loss - trade.entry_price) * trade.quantity
                trade.status = "closed_sl"
                trade.close_price = trade.stop_loss
                trade.pnl_usdt = round(pnl, 4)
                trade.closed_at = datetime.now(timezone.utc).isoformat()
                closed = True
                logger.info(
                    "%s LONG: Stop loss hit. Price=%.4f <= SL=%.4f. PnL=%.2f USDT",
                    trade.symbol, current_price, trade.stop_loss, pnl,
                )
            elif current_price >= trade.take_profit:
                pnl = (trade.take_profit - trade.entry_price) * trade.quantity
                trade.status = "closed_tp"
                trade.close_price = trade.take_profit
                trade.pnl_usdt = round(pnl, 4)
                trade.closed_at = datetime.now(timezone.utc).isoformat()
                closed = True
                logger.info(
                    "%s LONG: Take profit hit. Price=%.4f >= TP=%.4f. PnL=%.2f USDT",
                    trade.symbol, current_price, trade.take_profit, pnl,
                )

        elif trade.direction == "short":
            if current_price >= trade.stop_loss:
                pnl = (trade.entry_price - trade.stop_loss) * trade.quantity
                trade.status = "closed_sl"
                trade.close_price = trade.stop_loss
                trade.pnl_usdt = round(-pnl, 4)
                trade.closed_at = datetime.now(timezone.utc).isoformat()
                closed = True
                logger.info(
                    "%s SHORT: Stop loss hit. Price=%.4f >= SL=%.4f. PnL=%.2f USDT",
                    trade.symbol, current_price, trade.stop_loss, -pnl,
                )
            elif current_price <= trade.take_profit:
                pnl = (trade.entry_price - trade.take_profit) * trade.quantity
                trade.status = "closed_tp"
                trade.close_price = trade.take_profit
                trade.pnl_usdt = round(pnl, 4)
                trade.closed_at = datetime.now(timezone.utc).isoformat()
                closed = True
                logger.info(
                    "%s SHORT: Take profit hit. Price=%.4f <= TP=%.4f. PnL=%.2f USDT",
                    trade.symbol, current_price, trade.take_profit, pnl,
                )

        if closed:
            store.close_trade(trade)
            closed_this_cycle.append(trade)
        else:
            logger.info(
                "%s %s: Monitoring. Price=%.4f | SL=%.4f | TP=%.4f",
                trade.symbol, trade.direction.upper(), current_price,
                trade.stop_loss, trade.take_profit,
            )

    return closed_this_cycle
