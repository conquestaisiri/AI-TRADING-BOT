"""
Judge Agent — single final review model.

Receives the signal packet and all pod consensus objects.
Returns a structured JudgeResponse with verdict: approve | reject | cautious.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ai.config import AIConfig
from ai.schemas import JudgeResponse, PodConsensus, SignalPacket
from ai.prompts import JUDGE_SYSTEM, JUDGE_USER, build_pod_summary
from ai.provider import call_model, parse_json_response, ProviderError
from events.bus import emit
from logs.logger import get_logger

logger = get_logger("ai.judge")

REQUIRED_KEYS = ["verdict", "confidence", "strengths", "weaknesses", "risk_flags", "concise_reason"]
VALID_VERDICTS = {"approve", "reject", "cautious"}


def run_judge(
    signal: SignalPacket,
    pod_results: list[PodConsensus],
    config: AIConfig,
) -> JudgeResponse:
    """
    Call the Judge Agent and return a JudgeResponse.
    Never raises — on failure returns a JudgeResponse with error set.
    """
    emit("judge_start", {
        "symbol": signal.symbol,
        "model": config.judge_model,
        "provider": config.judge_provider,
    })
    logger.info("[Judge] Starting review for %s using %s", signal.symbol, config.judge_model)

    pod_summary = build_pod_summary(pod_results)
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM},
        {
            "role": "user",
            "content": JUDGE_USER.format(
                signal_summary=signal.to_prompt_summary(),
                pod_summary=pod_summary,
            ),
        },
    ]

    t0 = time.time()
    try:
        raw = call_model(config.judge_provider, config.judge_model, messages, config)
        latency = int((time.time() - t0) * 1000)

        parsed = parse_json_response(raw, REQUIRED_KEYS)
        verdict = str(parsed.get("verdict", "reject")).lower()
        if verdict not in VALID_VERDICTS:
            verdict = "reject"

        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.5))))
        strengths = [str(s) for s in parsed.get("strengths", [])]
        weaknesses = [str(w) for w in parsed.get("weaknesses", [])]
        risk_flags = [str(r) for r in parsed.get("risk_flags", [])]
        concise_reason = str(parsed.get("concise_reason", ""))

        result = JudgeResponse(
            model_name=config.judge_model,
            provider=config.judge_provider,
            verdict=verdict,  # type: ignore[arg-type]
            confidence=confidence,
            strengths=strengths,
            weaknesses=weaknesses,
            risk_flags=risk_flags,
            concise_reason=concise_reason,
            latency_ms=latency,
            error=None,
        )

        emit("judge_result", {
            "symbol": signal.symbol,
            "verdict": verdict,
            "confidence": confidence,
            "concise_reason": concise_reason,
            "latency_ms": latency,
        })
        logger.info("[Judge] %s → verdict=%s (conf=%.2f) in %dms: %s",
                    signal.symbol, verdict, confidence, latency, concise_reason)
        return result

    except ProviderError as pe:
        latency = int((time.time() - t0) * 1000)
        logger.error("[Judge] Provider error for %s: %s", signal.symbol, pe)
        emit("judge_error", {"symbol": signal.symbol, "error": str(pe)})
        return JudgeResponse(
            model_name=config.judge_model,
            provider=config.judge_provider,
            verdict="reject",
            confidence=0.0,
            strengths=[],
            weaknesses=["Judge agent unavailable"],
            risk_flags=["AI review could not complete"],
            concise_reason=f"Judge unavailable: {pe}",
            latency_ms=latency,
            error=str(pe),
        )
    except Exception as exc:
        latency = int((time.time() - t0) * 1000)
        logger.error("[Judge] Unexpected error for %s: %s", signal.symbol, exc, exc_info=True)
        emit("judge_error", {"symbol": signal.symbol, "error": str(exc)})
        return JudgeResponse(
            model_name=config.judge_model,
            provider=config.judge_provider,
            verdict="reject",
            confidence=0.0,
            strengths=[],
            weaknesses=["Unexpected error in judge"],
            risk_flags=["AI review failed"],
            concise_reason=f"Judge error: {exc}",
            latency_ms=latency,
            error=str(exc),
        )
