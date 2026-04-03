import sys
import os
import time

sys.path.insert(0, os.path.dirname(__file__))

from logs.logger import get_logger
from config.settings import settings
from exchange.connector import create_exchange, fetch_balance
from data.market_data import fetch_all_ohlcv
from features.indicators import enrich_all
from strategy.signal import detect_all_setups
from risk.calculator import calculate_trade_parameters
from execution.order_executor import execute_demo_order
from monitoring.position_monitor import monitor_open_trades
from storage.trade_store import TradeStore

logger = get_logger("app")


def run_loop(store: TradeStore) -> None:
    logger.info("=" * 60)
    logger.info("Crypto Demo Trading Bot starting up.")
    logger.info("Symbols: %s", settings.SYMBOLS)
    logger.info("Timeframes: trend=%s, entry=%s", settings.TIMEFRAME_TREND, settings.TIMEFRAME_ENTRY)
    logger.info("Risk: %.2f%%, R:R = %.1f", settings.RISK_PERCENT, settings.REWARD_TO_RISK)
    logger.info("=" * 60)

    exchange = create_exchange()

    balance = fetch_balance(exchange)
    if balance <= 0:
        logger.warning(
            "Testnet USDT balance is %.2f. Using configured starting balance: %.2f",
            balance,
            settings.STARTING_DEMO_BALANCE_USDT,
        )
        balance = settings.STARTING_DEMO_BALANCE_USDT

    iteration = 0

    while True:
        iteration += 1
        logger.info("--- Loop iteration %d ---", iteration)

        try:
            monitor_open_trades(exchange, store)

            ohlcv_map = fetch_all_ohlcv(exchange)

            enriched = enrich_all(
                ohlcv_map,
                ema_fast=settings.EMA_FAST,
                ema_slow=settings.EMA_SLOW,
                rsi_period=settings.RSI_PERIOD,
                atr_period=settings.ATR_PERIOD,
                volume_avg_period=settings.VOLUME_AVG_PERIOD,
                swing_lookback=settings.SWING_LOOKBACK,
            )

            setups = detect_all_setups(
                enriched,
                timeframe_trend=settings.TIMEFRAME_TREND,
                timeframe_entry=settings.TIMEFRAME_ENTRY,
            )

            for setup in setups:
                if store.has_open_trade_for_symbol(setup.symbol):
                    logger.info(
                        "%s: Open trade already exists — skipping new setup.",
                        setup.symbol,
                    )
                    continue

                balance = fetch_balance(exchange)
                if balance <= 0:
                    logger.warning(
                        "Balance is %.2f from exchange. Falling back to starting balance.",
                        balance,
                    )
                    balance = settings.STARTING_DEMO_BALANCE_USDT

                params = calculate_trade_parameters(
                    setup=setup,
                    balance_usdt=balance,
                    risk_percent=settings.RISK_PERCENT,
                    reward_to_risk=settings.REWARD_TO_RISK,
                )

                if params is None:
                    logger.warning(
                        "%s: Risk calculation returned no valid parameters. Skipping.",
                        setup.symbol,
                    )
                    continue

                trade = execute_demo_order(exchange, params, store)
                if trade:
                    logger.info(
                        "New trade entered: %s [%s] Entry=%.4f SL=%.4f TP=%.4f",
                        trade.symbol, trade.direction,
                        trade.entry_price, trade.stop_loss, trade.take_profit,
                    )

        except RuntimeError as exc:
            logger.error("Runtime error in main loop: %s", exc)
        except Exception as exc:
            logger.error("Unexpected error in main loop: %s", exc, exc_info=True)

        logger.info(
            "Loop iteration %d complete. Sleeping %ds until next cycle.",
            iteration,
            settings.LOOP_INTERVAL_SECONDS,
        )
        time.sleep(settings.LOOP_INTERVAL_SECONDS)


def main() -> None:
    store = TradeStore()
    run_loop(store)


if __name__ == "__main__":
    main()
