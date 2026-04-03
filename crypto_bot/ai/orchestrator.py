"""
AI Orchestrator — deterministic pod selection and execution.

Orchestration modes:
  light    → Trend + Risk pods only (fastest, lowest cost)
  standard → Trend + Structure + Risk pods
  full     → All 5 pods
  auto     → score-based selection:
               score < min_score_for_review → don't call AI (reject before AI)
               score < 0.65 → light
               score < 0.80 → standard
               score >= 0.80 → full

The orchestrator never runs forever — all pod calls are bounded by the
provider timeout and the concurrent executor.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from ai.config import AIConfig
from ai.schemas import PodConsensus, SignalPacket
from ai.pods.trend_pod import TrendPod
from ai.pods.structure_pod import StructurePod
from ai.pods.regime_pod import RegimePod
from ai.pods.risk_pod import RiskPod
from ai.pods.execution_pod import ExecutionPod
from events.bus import emit
from logs.logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger("ai.orchestrator")

_ALL_POD_NAMES = ["trend", "structure", "regime", "risk", "execution"]
_LIGHT_PODS = ["trend", "risk"]
_STANDARD_PODS = ["trend", "structure", "risk"]
_FULL_PODS = ["trend", "structure", "regime", "risk", "execution"]


def _select_pods(mode: str, rule_score: float, config: AIConfig) -> list[str]:
    """Return the list of pod names to run given mode and score."""
    if mode == "light":
        return _LIGHT_PODS
    if mode == "standard":
        return _STANDARD_PODS
    if mode == "full":
        return _FULL_PODS
    # auto
    if rule_score < config.min_score_for_review:
        return []  # pre-reject, don't call AI at all
    if rule_score < 0.65:
        return _LIGHT_PODS
    if rule_score < 0.80:
        return _STANDARD_PODS
    return _FULL_PODS


def _disabled_consensus(pod_name: str, config: AIConfig) -> PodConsensus:
    """Return a placeholder consensus for a pod that was not called."""
    pod_cfg = getattr(config, f"{pod_name}_pod")
    return PodConsensus(
        pod_name=pod_name,
        model_a_name=pod_cfg.model_a,
        model_b_name=pod_cfg.model_b,
        model_a_provider=pod_cfg.provider_a,
        model_b_provider=pod_cfg.provider_b,
        model_a_decision="not_called",
        model_b_decision="not_called",
        model_a_confidence=0.0,
        model_b_confidence=0.0,
        model_a_reasoning="Pod not invoked in this orchestration run",
        model_b_reasoning="Pod not invoked in this orchestration run",
        agreement_score=1.0,
        conflict_flag=False,
        pod_decision="not_called",
        pod_confidence=0.0,
        latency_ms=0,
        fallback_used=False,
        status="disabled",
        error=None,
    )


def run_council(
    signal: SignalPacket,
    config: AIConfig,
    mode_override: str | None = None,
) -> list[PodConsensus]:
    """
    Run the AI pod council for a signal.

    Returns a list of 5 PodConsensus objects (one per pod, in fixed order).
    Pods not invoked have status="disabled".
    """
    mode = mode_override or config.orchestration_mode
    pod_names = _select_pods(mode, signal.rule_score, config)

    emit("orchestrator_start", {
        "symbol": signal.symbol,
        "mode": mode,
        "rule_score": signal.rule_score,
        "pods_selected": pod_names,
    })

    if not pod_names:
        logger.info("[Orchestrator] Score %.3f below threshold — skipping all pods", signal.rule_score)
        emit("orchestrator_skip", {
            "symbol": signal.symbol,
            "reason": f"score {signal.rule_score:.3f} < min_score_for_review {config.min_score_for_review:.3f}",
        })
        return [_disabled_consensus(name, config) for name in _ALL_POD_NAMES]

    # Build pod instances for selected pods
    pod_map = {
        "trend":     TrendPod(config.trend_pod, config),
        "structure": StructurePod(config.structure_pod, config),
        "regime":    RegimePod(config.regime_pod, config),
        "risk":      RiskPod(config.risk_pod, config),
        "execution": ExecutionPod(config.execution_pod, config),
    }

    results: dict[str, PodConsensus] = {}

    def run_pod(name: str) -> tuple[str, PodConsensus]:
        emit("pod_start", {"pod": name, "symbol": signal.symbol,
                           "model_a": pod_map[name].pod_config.model_a,
                           "model_b": pod_map[name].pod_config.model_b})
        logger.info("[Orchestrator] Running %s pod for %s", name, signal.symbol)
        try:
            consensus = pod_map[name].run(signal)
        except Exception as exc:
            logger.error("[Orchestrator] %s pod crashed: %s", name, exc)
            consensus = _disabled_consensus(name, config)
            consensus.status = "failed"
            consensus.error = str(exc)

        emit("pod_result", {
            "pod": name,
            "symbol": signal.symbol,
            "decision": consensus.pod_decision,
            "confidence": consensus.pod_confidence,
            "agreement": consensus.agreement_score,
            "conflict": consensus.conflict_flag,
            "latency_ms": consensus.latency_ms,
            "status": consensus.status,
        })

        if consensus.conflict_flag:
            emit("pod_conflict", {
                "pod": name,
                "symbol": signal.symbol,
                "model_a_decision": consensus.model_a_decision,
                "model_b_decision": consensus.model_b_decision,
            })

        return name, consensus

    # Run selected pods concurrently
    with ThreadPoolExecutor(max_workers=len(pod_names)) as executor:
        futures = {executor.submit(run_pod, name): name for name in pod_names}
        for fut in futures:
            name, consensus = fut.result()
            results[name] = consensus

    # Add disabled placeholders for unrun pods
    for name in _ALL_POD_NAMES:
        if name not in results:
            results[name] = _disabled_consensus(name, config)

    ordered = [results[name] for name in _ALL_POD_NAMES]

    # Count conflicts
    conflicts = [p for p in ordered if p.conflict_flag]
    emit("orchestrator_complete", {
        "symbol": signal.symbol,
        "pods_run": len(pod_names),
        "conflicts": len(conflicts),
    })

    return ordered
