"""
Crypto Demo Trading Bot — Main Entry Point

Flow each cycle:
  1. Monitor open positions (SL/TP check)
  2. Check loss cooldown state
  3. Fetch fresh OHLCV candles for all symbols
  4. Calculate indicators (EMA, RSI, ATR, avg volume, shifted swing levels)
  5. Detect breakout/continuation setups (with regime and RSI filters)
  6. Size and execute demo trades on Binance Futures testnet
  7. Log all decisions with full context

Exchange: Binance Futures Testnet (supports real long AND short)
Credentials: https://testnet.binancefuture.com
"""

import sys
import os
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from logs.logger import get_logger
from config.settings import settings
from exchange.connector import create_exchange, fetch_usdt_balance
from data.market_data import fetch_all_ohlcv
from features.indicators import enrich_all, build_feature_summary
from strategy.signal import detect_all_setups, Setup
from risk.calculator import calculate_trade_parameters
from execution.order_executor import execute_demo_order_from_setup
from monitoring.position_monitor import monitor_open_trades
from storage.trade_store import TradeStore

logger = get_logger("app")

# Per-symbol consecutive-loss counter (resets when a TP is hit)
_consecutive_losses: dict[str, int] = defaultdict(int)
# Per-symbol cooldown loops remaining
_cooldown_loops: dict[str, int] = defaultdict(int)


def _update_loss_state(symbol: str, was_sl: bool) -> None:
    if was_sl:
        _consecutive_losses[symbol] += 1
        if _consecutive_losses[symbol] >= settings.LOSS_COOLDOWN_COUNT:
            _cooldown_loops[symbol] = settings.LOSS_COOLDOWN_LOOPS
            logger.warning(
                "%s: %d consecutive losses — cooling down for %d loop(s).",
                symbol, _consecutive_losses[symbol], settings.LOSS_COOLDOWN_LOOPS,
            )
    else:
        _consecutive_losses[symbol] = 0
        _cooldown_loops[symbol] = 0


def _in_cooldown(symbol: str) -> bool:
    if _cooldown_loops[symbol] > 0:
        _cooldown_loops[symbol] -= 1
        logger.info(
            "%s: In loss cooldown. Loops remaining after decrement: %d",
            symbol, _cooldown_loops[symbol],
        )
        return True
    return False


def startup_banner() -> None:
    logger.info("=" * 65)
    logger.info(" Crypto Demo Trading Bot — Binance Futures Testnet")
    logger.info("=" * 65)
    logger.info(" Symbols     : %s", ", ".join(settings.SYMBOLS))
    logger.info(" Trend TF    : %s | Entry TF: %s", settings.TIMEFRAME_TREND, settings.TIMEFRAME_ENTRY)
    logger.info(" Risk        : %.2f%% per trade", settings.RISK_PERCENT)
    logger.info(" Reward:Risk : %.1f", settings.REWARD_TO_RISK)
    logger.info(" ATR mult    : %.1f | RSI OB: %.0f | RSI OS: %.0f",
                settings.ATR_STOP_MULTIPLIER, settings.RSI_OVERBOUGHT, settings.RSI_OVERSOLD)
    logger.info(" EMA spread  : min %.2f%% | ATR min: %.2f%% of price",
                settings.EMA_MIN_SPREAD_PCT, settings.ATR_MIN_PCT)
    logger.info(" Loss CD     : %d losses → %d loop cooldown",
                settings.LOSS_COOLDOWN_COUNT, settings.LOSS_COOLDOWN_LOOPS)
    logger.info(" Loop every  : %ds", settings.LOOP_INTERVAL_SECONDS)
    logger.info("=" * 65)
    logger.info("")
    logger.info("IMPORTANT: This bot runs on Binance FUTURES testnet.")
    logger.info("Credentials must come from https://testnet.binancefuture.com")
    logger.info("(NOT the spot testnet — spot cannot short properly.)")
    logger.info("")


def run_loop(store: TradeStore) -> None:
    # Step 1: Validate config (clear error before anything else runs)
    settings.validate()

    startup_banner()

    # Step 2: Connect to exchange
    exchange = create_exchange()

    # Step 3: Get initial balance
    balance = fetch_usdt_balance(exchange)
    if balance <= 0:
        logger.warning(
            "Futures testnet balance is %.2f. Using fallback: %.2f USDT",
            balance, settings.STARTING_DEMO_BALANCE_USDT,
        )
        balance = settings.STARTING_DEMO_BALANCE_USDT

    iteration = 0

    while True:
        iteration += 1
        logger.info("")
        logger.info("─" * 55)
        logger.info(" CYCLE %d", iteration)
        logger.info("─" * 55)

        try:
            # --- Phase 1: Monitor open positions ---
            closed = monitor_open_trades(exchange, store)
            for closed_trade in closed:
                was_sl = closed_trade.status == "closed_sl"
                _update_loss_state(closed_trade.symbol, was_sl)

            # --- Phase 2: Refresh balance after closes ---
            fresh_balance = fetch_usdt_balance(exchange)
            if fresh_balance > 0:
                balance = fresh_balance
            else:
                logger.warning(
                    "Could not refresh balance from exchange. "
                    "Using last known: %.2f USDT", balance,
                )

            # --- Phase 3: Fetch market data ---
            ohlcv_map = fetch_all_ohlcv(exchange)

            # --- Phase 4: Calculate indicators ---
            enriched = enrich_all(
                ohlcv_map,
                ema_fast=settings.EMA_FAST,
                ema_slow=settings.EMA_SLOW,
                rsi_period=settings.RSI_PERIOD,
                atr_period=settings.ATR_PERIOD,
                volume_avg_period=settings.VOLUME_AVG_PERIOD,
                swing_lookback=settings.SWING_LOOKBACK,
            )

            # --- Phase 5: Log feature summaries (AI hook) ---
            for symbol, timeframes in enriched.items():
                for tf, df in timeframes.items():
                    try:
                        summary = build_feature_summary(df, symbol, tf)
                        logger.debug(
                            "FEATURE SUMMARY %s [%s]: %s",
                            symbol, tf, summary,
                        )
                    except Exception:
                        pass  # summary is informational only, never block trading

            # --- Phase 6: Detect setups ---
            setups, rejections = detect_all_setups(
                enriched,
                timeframe_trend=settings.TIMEFRAME_TREND,
                timeframe_entry=settings.TIMEFRAME_ENTRY,
            )

            # --- Phase 7: Process setups ---
            for setup in setups:

                if _in_cooldown(setup.symbol):
                    logger.info(
                        "%s: Skipping setup — symbol is in loss cooldown.",
                        setup.symbol,
                    )
                    continue

                if store.has_open_trade_for_symbol(setup.symbol):
                    logger.info(
                        "%s: Skipping setup — open trade already exists.",
                        setup.symbol,
                    )
                    continue

                params = calculate_trade_parameters(
                    setup=setup,
                    balance_usdt=balance,
                    risk_percent=settings.RISK_PERCENT,
                    reward_to_risk=settings.REWARD_TO_RISK,
                    atr_multiplier=settings.ATR_STOP_MULTIPLIER,
                )

                if params is None:
                    logger.warning(
                        "%s: Risk calculation returned no valid parameters. Skipping.",
                        setup.symbol,
                    )
                    continue

                trade = execute_demo_order_from_setup(
                    exchange=exchange,
                    params=params,
                    store=store,
                    candle_timestamp=setup.candle_timestamp,
                    trend_1h=setup.trend_1h,
                )

                if trade:
                    logger.info(
                        "NEW TRADE | %s [%s] | entry=%.4f | SL=%.4f | TP=%.4f | "
                        "qty=%.6f | candle=%s | trend=%s",
                        trade.symbol, trade.direction.upper(),
                        trade.entry_price, trade.stop_loss, trade.take_profit,
                        trade.quantity, trade.candle_timestamp, trade.trend_1h,
                    )

        except RuntimeError as exc:
            logger.error("Runtime error in main loop: %s", exc)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user (KeyboardInterrupt).")
            sys.exit(0)
        except Exception as exc:
            logger.error("Unexpected error in main loop: %s", exc, exc_info=True)

        logger.info(
            "Cycle %d complete. Sleeping %ds until next cycle.",
            iteration, settings.LOOP_INTERVAL_SECONDS,
        )
        time.sleep(settings.LOOP_INTERVAL_SECONDS)


def main() -> None:
    store = TradeStore()
    run_loop(store)


if __name__ == "__main__":
    main()
