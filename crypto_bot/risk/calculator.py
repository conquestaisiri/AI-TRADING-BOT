from dataclasses import dataclass
from strategy.signal import Setup, Direction
from logs.logger import get_logger

logger = get_logger("risk.calculator")


@dataclass
class TradeParameters:
    symbol: str
    direction: Direction
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_distance: float
    quantity: float
    risk_amount_usdt: float
    reward_amount_usdt: float
    risk_percent: float
    reward_to_risk: float
    atr: float
    atr_multiplier: float


def calculate_trade_parameters(
    setup: Setup,
    balance_usdt: float,
    risk_percent: float,
    reward_to_risk: float,
    atr_multiplier: float = 1.5,
) -> TradeParameters | None:
    """
    Calculate stop loss, take profit, and position size from a Setup.

    Stop loss distance = ATR * atr_multiplier
      Long:  stop_loss = entry - risk_distance
      Short: stop_loss = entry + risk_distance

    Take profit:
      Long:  take_profit = entry + risk_distance * reward_to_risk
      Short: take_profit = entry - risk_distance * reward_to_risk

    Quantity = risk_amount_usdt / risk_distance_per_unit

    Returns None with detailed log if any calculation is invalid.
    """
    if balance_usdt <= 0:
        logger.error(
            "%s: Cannot size trade — balance is %.2f USDT.",
            setup.symbol, balance_usdt,
        )
        return None

    if setup.atr <= 0:
        logger.error(
            "%s: Cannot size trade — ATR is %.6f (zero or negative).",
            setup.symbol, setup.atr,
        )
        return None

    risk_amount = balance_usdt * (risk_percent / 100.0)
    if risk_amount <= 0:
        logger.error(
            "%s: Risk amount computed as %.4f — invalid. balance=%.2f risk=%.4f%%",
            setup.symbol, risk_amount, balance_usdt, risk_percent,
        )
        return None

    risk_distance = setup.atr * atr_multiplier
    if risk_distance <= 0:
        logger.error(
            "%s: Risk distance is %.6f — invalid. ATR=%.6f multiplier=%.2f",
            setup.symbol, risk_distance, setup.atr, atr_multiplier,
        )
        return None

    if setup.direction == "long":
        stop_loss = setup.entry_price - risk_distance
        take_profit = setup.entry_price + risk_distance * reward_to_risk
    else:
        stop_loss = setup.entry_price + risk_distance
        take_profit = setup.entry_price - risk_distance * reward_to_risk

    if stop_loss <= 0:
        logger.error(
            "%s: Stop loss %.4f is <= 0 — nonsensical. entry=%.4f risk_dist=%.6f",
            setup.symbol, stop_loss, setup.entry_price, risk_distance,
        )
        return None

    if take_profit <= 0:
        logger.error(
            "%s: Take profit %.4f is <= 0 — nonsensical.",
            setup.symbol, take_profit,
        )
        return None

    quantity = risk_amount / risk_distance
    if quantity <= 0:
        logger.error("%s: Computed quantity %.8f is invalid.", setup.symbol, quantity)
        return None

    # Cap position size to available balance
    position_cost = quantity * setup.entry_price
    if position_cost > balance_usdt:
        original_qty = quantity
        quantity = balance_usdt / setup.entry_price
        logger.warning(
            "%s: Position cost %.2f USDT > balance %.2f USDT. "
            "Capped quantity from %.6f to %.6f.",
            setup.symbol, position_cost, balance_usdt, original_qty, quantity,
        )

    reward_amount = risk_amount * reward_to_risk

    logger.info(
        "%s: RISK PARAMS | direction=%s | entry=%.4f | SL=%.4f | TP=%.4f | "
        "risk_dist=%.4f | qty=%.6f | risk=%.2f USDT | reward=%.2f USDT | "
        "ATR=%.4f x %.1f",
        setup.symbol, setup.direction,
        setup.entry_price, stop_loss, take_profit,
        risk_distance, quantity,
        risk_amount, reward_amount,
        setup.atr, atr_multiplier,
    )

    return TradeParameters(
        symbol=setup.symbol,
        direction=setup.direction,
        entry_price=setup.entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_distance=risk_distance,
        quantity=quantity,
        risk_amount_usdt=risk_amount,
        reward_amount_usdt=reward_amount,
        risk_percent=risk_percent,
        reward_to_risk=reward_to_risk,
        atr=setup.atr,
        atr_multiplier=atr_multiplier,
    )
