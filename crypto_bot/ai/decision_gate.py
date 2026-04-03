"""
Decision Gate — final gate that combines:
  1. Rule-based signal approval (from the 7-stage pipeline)
  2. Trade score threshold check
  3. AI pod council results
  4. Judge agent verdict

Returns a FinalDecision: approved or rejected, with full audit trail.

This gate is additive — it never approves a signal that the rule system rejected.
It can only reject signals that passed the rule system.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ai.config import AIConfig
from ai.schemas import FinalDecision, PodConsensus, SignalPacket
from ai.orchestrator import run_council
from ai.judge import run_judge
from events.bus import emit, write_state
from logs.logger import get_logger

if TYPE_CHECKING:
    from strategy.signal import SignalEvaluation

logger = get_logger("ai.decision_gate")


def _rule_score_from_signal(signal: "SignalEvaluation") -> float:
    """
    Derive a normalized rule score (0.0 – 1.0) from a SignalEvaluation.
    This is a composite of regime score and quality metrics.
    """
    score = 0.0

    # Regime score (0.0 – 1.0) — 40% weight
    if signal.regime_score is not None:
        score += signal.regime_score * 0.40

    # Volume ratio — 20% weight (cap at 3x → score 1.0)
    if signal.volume_ratio is not None:
        score += min(signal.volume_ratio / 3.0, 1.0) * 0.20

    # Breakout buffer — 20% weight (cap at 0.5 ATR → score 1.0)
    if signal.close_buffer_atr is not None:
        score += min(signal.close_buffer_atr / 0.5, 1.0) * 0.20

    # Body quality — 20% weight
    if signal.body_to_range_ratio is not None:
        score += min(signal.body_to_range_ratio, 1.0) * 0.20

    return round(min(score, 1.0), 4)


def _build_signal_packet(signal: "SignalEvaluation", rule_score: float) -> SignalPacket:
    return SignalPacket(
        symbol=signal.symbol,
        direction=signal.direction or "unknown",
        evaluated_at=signal.evaluated_at,
        regime_label=signal.regime_label,
        regime_score=signal.regime_score or 0.0,
        trend_state=signal.trend_state,
        ema_spread_pct=signal.ema_spread_pct or 0.0,
        breakout_level=signal.breakout_level or 0.0,
        close_vs_level=signal.close_vs_level or 0.0,
        close_buffer_atr=signal.close_buffer_atr or 0.0,
        volume_ratio=signal.volume_ratio or 0.0,
        body_to_range_ratio=signal.body_to_range_ratio or 0.0,
        body_atr_ratio=signal.body_atr_ratio or 0.0,
        has_rejection_wick=signal.has_rejection_wick or False,
        entry_price=signal.entry_price or 0.0,
        atr=signal.atr or 0.0,
        atr_pct=signal.atr_pct or 0.0,
        rsi=signal.rsi or 50.0,
        distance_from_ema_atr=signal.distance_from_ema_atr or 0.0,
        stop_loss=signal.stop_loss or 0.0,
        take_profit=signal.take_profit or 0.0,
        quantity=signal.quantity or 0.0,
        risk_amount_usdt=signal.risk_amount_usdt or 0.0,
        reward_amount_usdt=signal.reward_amount_usdt or 0.0,
        rule_score=rule_score,
    )


def evaluate(
    signal: "SignalEvaluation",
    config: AIConfig,
) -> FinalDecision:
    """
    Run the complete AI decision gate for an approved rule-based signal.

    The gate ONLY runs for signals that passed the 7-stage rule pipeline.
    Returns FinalDecision with approved=True|False.
    """
    now = datetime.now(timezone.utc).isoformat()

    # Gate 1: Must have passed rule-based evaluation
    if not signal.approved:
        return FinalDecision(
            approved=False,
            verdict="rejected",
            reason=f"Rule system rejected: {signal.rejection_code} — {signal.rejection_reason}",
            rule_score=0.0,
            pod_results=[],
            judge_result=None,
            ai_was_used=False,
            orchestration_mode=config.orchestration_mode,
            evaluated_at=now,
        )

    rule_score = _rule_score_from_signal(signal)

    emit("score_computed", {
        "symbol": signal.symbol,
        "rule_score": rule_score,
        "regime_score": signal.regime_score,
        "volume_ratio": signal.volume_ratio,
        "close_buffer_atr": signal.close_buffer_atr,
        "body_to_range_ratio": signal.body_to_range_ratio,
    })
    logger.info("[Gate] %s rule_score=%.3f", signal.symbol, rule_score)

    # Gate 2: Score below execution threshold even without AI
    if rule_score < config.min_score_for_execution:
        reason = f"Rule score {rule_score:.3f} < min_score_for_execution {config.min_score_for_execution:.3f}"
        emit("gate_reject", {"symbol": signal.symbol, "reason": reason, "stage": "score_threshold"})
        return FinalDecision(
            approved=False,
            verdict="rejected",
            reason=reason,
            rule_score=rule_score,
            pod_results=[],
            judge_result=None,
            ai_was_used=False,
            orchestration_mode=config.orchestration_mode,
            evaluated_at=now,
        )

    # Gate 3: AI disabled or no provider — rule-only pass
    if not config.ai_actually_enabled:
        reason = (
            "AI disabled (no provider key configured) — rule-only approval"
            if not config.has_any_provider
            else "AI_ENABLED=false — rule-only approval"
        )
        emit("gate_approve", {
            "symbol": signal.symbol,
            "mode": "rule_only",
            "rule_score": rule_score,
        })
        decision = FinalDecision(
            approved=True,
            verdict="rule_only",
            reason=reason,
            rule_score=rule_score,
            pod_results=[],
            judge_result=None,
            ai_was_used=False,
            orchestration_mode="rule_only",
            evaluated_at=now,
        )
        write_state("ai_council", decision.to_dict())
        return decision

    # Gate 4: Run AI pod council
    packet = _build_signal_packet(signal, rule_score)
    pod_results = run_council(packet, config)

    # Count negative signals
    negative_pods = _count_negative_pods(pod_results)
    conflicts = [p for p in pod_results if p.conflict_flag and p.status == "complete"]
    failed_pods = [p for p in pod_results if p.status == "failed"]

    # Gate 5: Hard reject if too many negative or failed
    if negative_pods >= 3:
        reason = f"{negative_pods} pods returned negative verdict"
        emit("gate_reject", {"symbol": signal.symbol, "reason": reason, "stage": "pod_majority"})
        decision = FinalDecision(
            approved=False, verdict="rejected", reason=reason,
            rule_score=rule_score, pod_results=pod_results,
            judge_result=None, ai_was_used=True,
            orchestration_mode=config.orchestration_mode, evaluated_at=now,
        )
        write_state("ai_council", decision.to_dict())
        return decision

    # Gate 6: Run judge if required or if there are conflicts
    need_judge = (
        config.require_judge_approval
        or len(conflicts) > 0
        or negative_pods >= 2
    )
    judge_result = None
    if need_judge:
        judge_result = run_judge(packet, pod_results, config)

    # Gate 7: Final verdict
    approved, verdict, reason = _apply_final_logic(
        config, rule_score, pod_results, judge_result,
        negative_pods, conflicts, failed_pods,
    )

    emit("gate_decision", {
        "symbol": signal.symbol,
        "approved": approved,
        "verdict": verdict,
        "reason": reason,
        "rule_score": rule_score,
        "negative_pods": negative_pods,
        "conflicts": len(conflicts),
    })

    decision = FinalDecision(
        approved=approved, verdict=verdict, reason=reason,
        rule_score=rule_score, pod_results=pod_results,
        judge_result=judge_result, ai_was_used=True,
        orchestration_mode=config.orchestration_mode, evaluated_at=now,
    )
    write_state("ai_council", decision.to_dict())
    return decision


def _count_negative_pods(pod_results: list[PodConsensus]) -> int:
    negative_decisions = {
        "trend": "neutral",
        "structure": "failed_breakout",
        "regime": "unfavorable",
        "risk": "unacceptable",
        "execution": "unsafe",
    }
    count = 0
    for pod in pod_results:
        if pod.status not in ("complete", "partial"):
            continue
        neg = negative_decisions.get(pod.pod_name, "")
        if pod.pod_decision == neg:
            count += 1
    return count


def _apply_final_logic(
    config: AIConfig,
    rule_score: float,
    pod_results: list[PodConsensus],
    judge_result,
    negative_pods: int,
    conflicts: list,
    failed_pods: list,
) -> tuple[bool, str, str]:
    """Apply final approval logic. Returns (approved, verdict, reason)."""

    if judge_result is not None:
        verdict = judge_result.verdict
        reason = judge_result.concise_reason

        if verdict == "approve":
            if config.require_judge_approval:
                return True, "approved", f"Judge approved: {reason}"
            return True, "approved", f"Judge approved: {reason}"

        if verdict == "reject":
            if config.allow_rule_only_fallback and not config.require_judge_approval:
                return True, "cautious_pass", f"Judge cautious (fallback rule-only): {reason}"
            return False, "rejected", f"Judge rejected: {reason}"

        if verdict == "cautious":
            if config.allow_rule_only_fallback:
                return True, "cautious_pass", f"Cautious approval: {reason}"
            return False, "rejected", f"Judge cautious — rejected per config: {reason}"

    # No judge: decide from pod signals
    if negative_pods == 0:
        return True, "approved", "All pods positive, no judge needed"
    if negative_pods == 1 and len(conflicts) == 0:
        return True, "cautious_pass", f"1 negative pod, no conflicts — cautious approval"
    if negative_pods >= 2:
        if config.allow_rule_only_fallback and not config.require_judge_approval:
            return True, "cautious_pass", f"{negative_pods} negative pods — cautious fallback"
        return False, "rejected", f"{negative_pods} negative pods — rejected"

    return True, "approved", "Pods sufficient for approval"
