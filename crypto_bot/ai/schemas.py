"""
Structured schemas for the AI pod system.
Uses Python dataclasses — no external dependencies required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ─────────────────────────────────────────────────────────────────────────────
# Signal packet — structured input to all pods
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SignalPacket:
    symbol: str
    direction: str                  # "long" | "short"
    evaluated_at: str               # ISO UTC timestamp

    # Regime
    regime_label: str               # "trending" | "ranging" | "choppy"
    regime_score: float             # 0.0 – 1.0

    # Trend
    trend_state: str                # "bullish" | "bearish" | "neutral"
    ema_spread_pct: float           # EMA20–EMA50 spread as % of price

    # Breakout
    breakout_level: float
    close_vs_level: float           # how far close cleared the level
    close_buffer_atr: float         # close_vs_level / ATR

    # Quality
    volume_ratio: float             # volume / avg_volume
    body_to_range_ratio: float
    body_atr_ratio: float
    has_rejection_wick: bool

    # Price context
    entry_price: float
    atr: float
    atr_pct: float
    rsi: float
    distance_from_ema_atr: float

    # Risk
    stop_loss: float
    take_profit: float
    quantity: float
    risk_amount_usdt: float
    reward_amount_usdt: float

    # Rule score (0.0 – 1.0, derived from regime_score + quality metrics)
    rule_score: float

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    def to_prompt_summary(self) -> str:
        return (
            f"Symbol: {self.symbol}\n"
            f"Direction: {self.direction.upper()}\n"
            f"Entry: {self.entry_price:.4f} | SL: {self.stop_loss:.4f} | TP: {self.take_profit:.4f}\n"
            f"Regime: {self.regime_label} (score={self.regime_score:.3f})\n"
            f"Trend: {self.trend_state} (EMA spread={self.ema_spread_pct:.3f}%)\n"
            f"RSI: {self.rsi:.1f} | ATR%: {self.atr_pct:.4f}%\n"
            f"Breakout buffer: {self.close_buffer_atr:.3f} ATR | Volume ratio: {self.volume_ratio:.2f}x\n"
            f"Body/range: {self.body_to_range_ratio:.3f} | Body/ATR: {self.body_atr_ratio:.3f}\n"
            f"Distance from EMA20: {self.distance_from_ema_atr:.3f} ATR\n"
            f"Rejection wick: {'YES' if self.has_rejection_wick else 'NO'}\n"
            f"Rule score: {self.rule_score:.3f}\n"
            f"Risk: {self.risk_amount_usdt:.2f} USDT → Reward: {self.reward_amount_usdt:.2f} USDT"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Pod model response — raw output from a single model call
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PodModelResponse:
    model_name: str
    provider: str
    decision: str                   # pod-specific: e.g. "bullish" | "bearish" | "neutral"
    confidence: float               # 0.0 – 1.0
    reasoning: str                  # one-line explanation
    raw_output: str                 # full raw text from model
    latency_ms: int
    error: str | None = None        # set if call failed
    fallback_used: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Pod consensus — synthesized from model A + model B
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PodConsensus:
    pod_name: str

    model_a_name: str
    model_b_name: str
    model_a_provider: str
    model_b_provider: str

    model_a_decision: str
    model_b_decision: str
    model_a_confidence: float
    model_b_confidence: float
    model_a_reasoning: str
    model_b_reasoning: str

    agreement_score: float          # 1.0 = full agreement, 0.0 = complete conflict
    conflict_flag: bool

    pod_decision: str               # synthesized decision
    pod_confidence: float

    latency_ms: int                 # total wall time for both calls
    fallback_used: bool

    status: str = "complete"        # "complete" | "partial" | "failed" | "disabled"
    error: str | None = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


# ─────────────────────────────────────────────────────────────────────────────
# Judge response — final verdict from the judge agent
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class JudgeResponse:
    model_name: str
    provider: str
    verdict: Literal["approve", "reject", "cautious"]
    confidence: float               # 0.0 – 1.0
    strengths: list[str]
    weaknesses: list[str]
    risk_flags: list[str]
    concise_reason: str
    latency_ms: int
    error: str | None = None

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        return d


# ─────────────────────────────────────────────────────────────────────────────
# Final decision — gate output
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FinalDecision:
    approved: bool
    verdict: str                    # "approved" | "rejected" | "cautious_pass" | "rule_only"
    reason: str
    rule_score: float
    pod_results: list[PodConsensus]
    judge_result: JudgeResponse | None
    ai_was_used: bool
    orchestration_mode: str
    evaluated_at: str

    def to_dict(self) -> dict:
        d = {
            "approved": self.approved,
            "verdict": self.verdict,
            "reason": self.reason,
            "rule_score": self.rule_score,
            "ai_was_used": self.ai_was_used,
            "orchestration_mode": self.orchestration_mode,
            "evaluated_at": self.evaluated_at,
            "pod_results": [p.to_dict() for p in self.pod_results],
            "judge_result": self.judge_result.to_dict() if self.judge_result else None,
        }
        return d


# ─────────────────────────────────────────────────────────────────────────────
# UI event stream record
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class UIEventRecord:
    ts: int                         # Unix ms
    event_type: str
    message: str
    level: str = "info"             # "info" | "warn" | "error" | "signal" | "order" | "ai"
    metadata: dict = field(default_factory=dict)
