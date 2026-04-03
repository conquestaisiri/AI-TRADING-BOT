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
    quantity: float
    risk_amount_usdt: float
    reward_amount_usdt: float
    risk_percent: float
    reward_to_risk: float


def calculate_trade_parameters(
    setup: Setup,
    balance_usdt: float,
    risk_percent: float,
    reward_to_risk: float,
    atr_multiplier: float = 1.5,
) -> TradeParameters | None:
    """
    Calculate stop loss, take profit, and position size from a Setup.

    Stop loss is ATR-based:
      Long:  entry - (ATR * atr_multiplier)
      Short: entry + (ATR * atr_multiplier)

    Take profit uses reward_to_risk ratio:
      Long:  entry + risk_distance * reward_to_risk
      Short: entry - risk_distance * reward_to_risk

    Position size = risk_amount / risk_distance_per_unit

    Returns None if the parameters are invalid (zero ATR, invalid balance, etc.)
    """
    if balance_usdt <= 0:
        logger.error("Invalid balance: %.2f. Cannot size trade.", balance_usdt)
        return None

    if setup.atr <= 0:
        logger.error("%s: ATR is zero or negative (%.6f). Cannot size trade.", setup.symbol, setup.atr)
        return None

    risk_amount = balance_usdt * (risk_percent / 100.0)
    if risk_amount <= 0:
        logger.error(
            "%s: Computed risk amount is %.4f. balance=%.2f, risk_percent=%.4f",
            setup.symbol, risk_amount, balance_usdt, risk_percent,
        )
        return None

    risk_distance = setup.atr * atr_multiplier

    if risk_distance <= 0:
        logger.error("%s: Risk distance is zero. ATR=%.6f, multiplier=%.2f", setup.symbol, setup.atr, atr_multiplier)
        return None

    if setup.direction == "long":
        stop_loss = setup.entry_price - risk_distance
        take_profit = setup.entry_price + risk_distance * reward_to_risk
    else:
        stop_loss = setup.entry_price + risk_distance
        take_profit = setup.entry_price - risk_distance * reward_to_risk

    if stop_loss <= 0 or take_profit <= 0:
        logger.error(
            "%s: Invalid stop/tp levels. SL=%.4f, TP=%.4f. Skipping.",
            setup.symbol, stop_loss, take_profit,
        )
        return None

    quantity = risk_amount / risk_distance

    if quantity <= 0:
        logger.error("%s: Computed quantity is %.8f. Invalid.", setup.symbol, quantity)
        return None

    cost = quantity * setup.entry_price
    if cost > balance_usdt:
        logger.warning(
            "%s: Trade cost %.2f USDT exceeds balance %.2f USDT. Adjusting quantity.",
            setup.symbol, cost, balance_usdt,
        )
        quantity = balance_usdt / setup.entry_price

    reward_amount = risk_amount * reward_to_risk

    logger.info(
        "%s: Trade params — Direction=%s, Entry=%.4f, SL=%.4f, TP=%.4f, "
        "Qty=%.6f, Risk=%.2f USDT, Reward=%.2f USDT",
        setup.symbol,
        setup.direction,
        setup.entry_price,
        stop_loss,
        take_profit,
        quantity,
        risk_amount,
        reward_amount,
    )

    return TradeParameters(
        symbol=setup.symbol,
        direction=setup.direction,
        entry_price=setup.entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        quantity=quantity,
        risk_amount_usdt=risk_amount,
        reward_amount_usdt=reward_amount,
        risk_percent=risk_percent,
        reward_to_risk=reward_to_risk,
    )
