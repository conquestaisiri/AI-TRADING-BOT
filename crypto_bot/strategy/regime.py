"""
Market Regime Classifier

Classifies current market conditions as trending, ranging, or choppy using
rule-based logic derived from existing indicator columns. No AI required.

Score factors (each 0.0–0.25, total 0.0–1.0):
  1. EMA spread strength   — how wide apart are EMA20 and EMA50?
  2. EMA slope consistency — is EMA20 sloping in the expected direction?
  3. ATR expansion         — is volatility expanding vs its own moving average?
  4. Price-EMA alignment   — is price on the correct side of both EMAs?

Label thresholds:
  score >= REGIME_MIN_TREND_SCORE : "trending"
  score >= 0.30                   : "ranging"
  score <  0.30                   : "choppy"
"""

from dataclasses import dataclass, field
import pandas as pd
from logs.logger import get_logger

logger = get_logger("strategy.regime")

# EMA spread % that constitutes a "full" trend signal (mapped to 1.0 for that factor)
_SPREAD_FULL_PCT = 0.60


@dataclass
class RegimeResult:
    label: str          # "trending" | "ranging" | "choppy"
    score: float        # 0.0 – 1.0

    # Raw inputs for logging / AI summary
    ema_spread_pct: float
    ema_fast_slope_pct: float
    atr_expanding: bool
    price_above_both_emas: bool   # True for bullish alignment
    price_below_both_emas: bool   # True for bearish alignment

    # Per-component contributions to score
    component_spread: float
    component_slope: float
    component_atr: float
    component_alignment: float

    details: dict = field(default_factory=dict)


def classify_regime(
    df_1h: pd.DataFrame,
    direction: str,  # "bullish" | "bearish"
    min_trend_score: float,
) -> RegimeResult:
    """
    Classify the market regime using the most recent valid 1h candle.

    Args:
        df_1h:            Enriched 1h DataFrame (must contain indicator columns).
        direction:        Expected trend direction from EMA alignment ("bullish"/"bearish").
        min_trend_score:  Minimum score to label as "trending".

    Returns:
        RegimeResult with label, score, and all component details.
    """
    required = ["ema_fast", "ema_slow", "ema_spread_pct",
                "ema_fast_slope_pct", "atr", "atr_ma", "atr_expanding", "close"]
    valid = df_1h.dropna(subset=required)

    if valid.empty:
        logger.warning("Regime: not enough valid 1h data to classify. Defaulting to 'choppy'.")
        return RegimeResult(
            label="choppy", score=0.0,
            ema_spread_pct=0.0, ema_fast_slope_pct=0.0,
            atr_expanding=False,
            price_above_both_emas=False, price_below_both_emas=False,
            component_spread=0.0, component_slope=0.0,
            component_atr=0.0, component_alignment=0.0,
            details={"error": "insufficient_data"},
        )

    row = valid.iloc[-1]

    ema_fast = float(row["ema_fast"])
    ema_slow = float(row["ema_slow"])
    close = float(row["close"])
    ema_spread_pct = float(row["ema_spread_pct"])
    ema_fast_slope_pct = float(row["ema_fast_slope_pct"])
    atr_expanding = bool(row["atr_expanding"])

    price_above_both = close > ema_fast and close > ema_slow
    price_below_both = close < ema_fast and close < ema_slow

    # ── Component 1: EMA spread (max 0.25) ───────────────────────────────────
    # Wider spread = stronger trend. Full credit at _SPREAD_FULL_PCT.
    spread_ratio = min(ema_spread_pct / _SPREAD_FULL_PCT, 1.0)
    component_spread = round(spread_ratio * 0.25, 4)

    # ── Component 2: EMA slope direction (max 0.25) ──────────────────────────
    # Slope must point in the expected direction. Magnitude adds partial credit.
    slope_threshold_pct = 0.005  # minimum slope % to be considered directional
    if direction == "bullish" and ema_fast_slope_pct > slope_threshold_pct:
        slope_strength = min(ema_fast_slope_pct / 0.05, 1.0)
        component_slope = round(slope_strength * 0.25, 4)
    elif direction == "bearish" and ema_fast_slope_pct < -slope_threshold_pct:
        slope_strength = min(abs(ema_fast_slope_pct) / 0.05, 1.0)
        component_slope = round(slope_strength * 0.25, 4)
    else:
        component_slope = 0.0  # slope not directional or wrong direction

    # ── Component 3: ATR expansion (max 0.25) ────────────────────────────────
    # Expanding ATR means momentum/volatility is with the move.
    component_atr = 0.25 if atr_expanding else 0.0

    # ── Component 4: Price-EMA alignment (max 0.25) ──────────────────────────
    # Price on the correct side of both EMAs confirms trend structure.
    if direction == "bullish" and price_above_both:
        component_alignment = 0.25
    elif direction == "bearish" and price_below_both:
        component_alignment = 0.25
    else:
        component_alignment = 0.0

    # ── Total score ───────────────────────────────────────────────────────────
    score = round(
        component_spread + component_slope + component_atr + component_alignment,
        4,
    )
    score = min(score, 1.0)

    # ── Label ─────────────────────────────────────────────────────────────────
    if score >= min_trend_score:
        label = "trending"
    elif score >= 0.30:
        label = "ranging"
    else:
        label = "choppy"

    details = {
        "close": close,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "direction": direction,
        "candle_ts": str(row.name),
    }

    logger.debug(
        "Regime [%s]: label=%s score=%.3f | spread=%.3f%%(+%.3f) "
        "slope_pct=%.4f%%(+%.3f) atr_exp=%s(+%.3f) aligned=%s(+%.3f)",
        direction, label, score,
        ema_spread_pct, component_spread,
        ema_fast_slope_pct, component_slope,
        atr_expanding, component_atr,
        price_above_both if direction == "bullish" else price_below_both,
        component_alignment,
    )

    return RegimeResult(
        label=label,
        score=score,
        ema_spread_pct=ema_spread_pct,
        ema_fast_slope_pct=ema_fast_slope_pct,
        atr_expanding=atr_expanding,
        price_above_both_emas=price_above_both,
        price_below_both_emas=price_below_both,
        component_spread=component_spread,
        component_slope=component_slope,
        component_atr=component_atr,
        component_alignment=component_alignment,
        details=details,
    )
