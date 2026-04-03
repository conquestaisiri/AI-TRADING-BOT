"""
Crypto Demo Trading Bot — Main Entry Point

Autonomous loop each cycle:
  1.  Monitor open positions (SL/TP check, unrealised PnL)
  2.  Refresh balance
  3.  Fetch fresh OHLCV candles for all symbols
  4.  Calculate indicators (EMA, RSI, ATR, slopes, candle metrics, swing levels)
  5.  Build AI-ready feature summaries (logged at DEBUG, ready for future AI layer)
  6.  Run 7-stage signal evaluation for each symbol:
        Stage 1: Data sufficiency
        Stage 2: 1h trend determination
        Stage 3: Regime classification (rule-based)
        Stage 4: Breakout candidate detection
        Stage 5: Breakout quality (body, close buffer, wick)
        Stage 6: Overextension + cooldown + trade frequency limits
        Stage 7: Risk calc and final approval
  7.  Execute approved signals as futures market orders on testnet
  8.  Log all decisions end-to-end

Exchange: Binance Futures Testnet  →  https://testnet.binancefuture.com
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from logs.logger import get_logger
from config.settings import settings
from exchange.connector import create_exchange, fetch_usdt_balance
from data.market_data import fetch_all_ohlcv
from features.indicators import enrich_all, build_feature_summary
from strategy.signal import evaluate_all_signals
from execution.order_executor import execute_from_signal
from monitoring.position_monitor import monitor_open_trades
from storage.trade_store import TradeStore

logger = get_logger("app")


def startup_banner() -> None:
    logger.info("=" * 68)
    logger.info(" Crypto Demo Trading Bot — Binance Futures Testnet")
    logger.info("=" * 68)
    logger.info(" Symbols        : %s", ", ".join(settings.SYMBOLS))
    logger.info(" Trend TF       : %s | Entry TF: %s", settings.TIMEFRAME_TREND, settings.TIMEFRAME_ENTRY)
    logger.info(" Risk           : %.2f%% | R:R = %.1f | ATR mult = %.1f",
                settings.RISK_PERCENT, settings.REWARD_TO_RISK, settings.ATR_STOP_MULTIPLIER)
    logger.info(" Volume thresh  : %.2fx avg | RSI OB/OS: %.0f / %.0f",
                settings.VOLUME_RATIO_THRESHOLD, settings.RSI_OVERBOUGHT, settings.RSI_OVERSOLD)
    logger.info(" EMA spread min : %.2f%% | ATR min: %.2f%% of price",
                settings.EMA_MIN_SPREAD_PCT, settings.ATR_MIN_PCT)
    logger.info(" Regime score   : min %.2f to trade", settings.REGIME_MIN_TREND_SCORE)
    logger.info(" Overextension  : body<%dx ATR | dist<%dx ATR from EMA",
                settings.MAX_BODY_ATR_RATIO, settings.MAX_DISTANCE_FROM_EMA_ATR_RATIO)
    logger.info(" Breakout qual  : close buffer >= %.2fx ATR | body/range >= %.2f",
                settings.BREAKOUT_CLOSE_BUFFER_RATIO, settings.MIN_BODY_TO_RANGE_RATIO)
    logger.info(" Cooldown       : loss=%d candles | win=%d candles",
                settings.LOSS_COOLDOWN_CANDLES, settings.WIN_COOLDOWN_CANDLES)
    logger.info(" Frequency      : max %d trades / %dmin | min gap %dmin",
                settings.MAX_TRADES_PER_WINDOW, settings.TRADE_WINDOW_MINUTES, settings.MIN_ENTRY_GAP_MINUTES)
    logger.info(" Loop interval  : %ds", settings.LOOP_INTERVAL_SECONDS)
    logger.info("=" * 68)
    logger.info("")
    logger.info("IMPORTANT: Credentials must be from https://testnet.binancefuture.com")
    logger.info("(Futures testnet supports both long AND short — Spot testnet does not.)")
    logger.info("")


def run_loop(store: TradeStore) -> None:
    # ── Step 1: Validate config before anything else ───────────────────────
    settings.validate()

    startup_banner()

    # ── Step 2: Connect to exchange ────────────────────────────────────────
    exchange = create_exchange()

    # ── Step 3: Get initial balance ────────────────────────────────────────
    balance = fetch_usdt_balance(exchange)
    if balance <= 0:
        logger.warning(
            "Testnet balance returned %.2f USDT. Using fallback: %.2f USDT",
            balance, settings.STARTING_DEMO_BALANCE_USDT,
        )
        balance = settings.STARTING_DEMO_BALANCE_USDT

    iteration = 0

    while True:
        iteration += 1
        logger.info("")
        logger.info("━" * 60)
        logger.info(" CYCLE %d  |  balance=%.2f USDT", iteration, balance)
        logger.info("━" * 60)

        try:
            # ── Phase 1: Monitor open positions ────────────────────────────
            closed_this_cycle = monitor_open_trades(exchange, store)

            if closed_this_cycle:
                logger.info(
                    "%d trade(s) closed this cycle: %s",
                    len(closed_this_cycle),
                    [(t.symbol, t.status, f"PnL={t.pnl_usdt:.2f}") for t in closed_this_cycle],
                )

            # ── Phase 2: Refresh balance ───────────────────────────────────
            fresh = fetch_usdt_balance(exchange)
            if fresh > 0:
                balance = fresh
            else:
                logger.warning(
                    "Balance fetch returned 0 — using last known: %.2f USDT", balance
                )

            # ── Phase 3: Fetch market data ─────────────────────────────────
            ohlcv_map = fetch_all_ohlcv(exchange)

            # ── Phase 4: Calculate indicators ─────────────────────────────
            enriched = enrich_all(
                ohlcv_map,
                ema_fast=settings.EMA_FAST,
                ema_slow=settings.EMA_SLOW,
                rsi_period=settings.RSI_PERIOD,
                atr_period=settings.ATR_PERIOD,
                atr_ma_period=settings.ATR_MA_PERIOD,
                ema_slope_period=settings.EMA_SLOPE_PERIOD,
                volume_avg_period=settings.VOLUME_AVG_PERIOD,
                swing_lookback=settings.SWING_LOOKBACK,
            )

            # ── Phase 5: Feature summaries (AI layer hook) ─────────────────
            for symbol, timeframes in enriched.items():
                for tf, df in timeframes.items():
                    try:
                        summary = build_feature_summary(df, symbol, tf)
                        logger.debug("FEATURE [%s/%s]: %s", symbol, tf, summary)
                    except Exception:
                        pass  # informational only, never blocks trading

            # ── Phase 6: 7-stage signal evaluation ────────────────────────
            all_signals = evaluate_all_signals(
                enriched=enriched,
                store=store,
                balance_usdt=balance,
                timeframe_trend=settings.TIMEFRAME_TREND,
                timeframe_entry=settings.TIMEFRAME_ENTRY,
            )

            # ── Phase 7: Execute approved signals ─────────────────────────
            for signal in all_signals:
                if not signal.approved:
                    continue

                if store.has_open_trade_for_symbol(signal.symbol):
                    logger.info(
                        "%s: Skipping approved signal — open trade already exists.",
                        signal.symbol,
                    )
                    continue

                trade = execute_from_signal(exchange, signal, store)

                if trade:
                    logger.info(
                        "NEW TRADE | %s [%s] | fill=%.4f | SL=%.4f | TP=%.4f | "
                        "qty=%.6f | regime=%s(%.2f) | trend=%s | candle=%s",
                        trade.symbol, trade.direction.upper(),
                        trade.entry_price, trade.stop_loss, trade.take_profit,
                        trade.quantity, trade.regime_label, trade.regime_score,
                        trade.trend_1h, trade.candle_timestamp,
                    )

        except RuntimeError as exc:
            logger.error("Runtime error in main loop: %s", exc)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user (Ctrl+C).")
            sys.exit(0)
        except Exception as exc:
            logger.error("Unexpected error in main loop: %s", exc, exc_info=True)

        logger.info(
            "Cycle %d complete. Sleeping %ds...",
            iteration, settings.LOOP_INTERVAL_SECONDS,
        )
        time.sleep(settings.LOOP_INTERVAL_SECONDS)


def main() -> None:
    store = TradeStore()
    run_loop(store)


if __name__ == "__main__":
    main()
