"""
Base pod — runs two models concurrently and synthesizes a PodConsensus.
All 5 specialist pods inherit from this.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from ai.schemas import PodConsensus, PodModelResponse
from ai.provider import call_model, parse_json_response, ProviderError
from logs.logger import get_logger

if TYPE_CHECKING:
    from ai.config import AIConfig, PodConfig
    from ai.schemas import SignalPacket

logger = get_logger("ai.pod")


class BasePod:
    POD_NAME: str = "base"
    REQUIRED_KEYS: list[str] = ["decision", "confidence", "reasoning"]
    VALID_DECISIONS: list[str] = []

    def __init__(self, pod_config: "PodConfig", ai_config: "AIConfig") -> None:
        self.pod_config = pod_config
        self.ai_config = ai_config

    @property
    def name(self) -> str:
        return self.POD_NAME

    def build_messages(self, signal: "SignalPacket") -> tuple[list[dict], list[dict]]:
        """Return (messages_for_model_a, messages_for_model_b)."""
        raise NotImplementedError

    def _call_one(
        self,
        model: str,
        provider: str,
        messages: list[dict],
        label: str,
    ) -> PodModelResponse:
        """Call one model and return a normalized PodModelResponse."""
        t0 = time.time()
        try:
            raw = call_model(provider, model, messages, self.ai_config)
            latency = int((time.time() - t0) * 1000)

            try:
                parsed = parse_json_response(raw, self.REQUIRED_KEYS)
                decision = str(parsed.get("decision", "unknown")).lower()
                if self.VALID_DECISIONS and decision not in self.VALID_DECISIONS:
                    decision = self.VALID_DECISIONS[-1]  # fallback to last (most conservative)
                confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.5))))
                reasoning = str(parsed.get("reasoning", ""))
            except (ValueError, KeyError, TypeError) as parse_err:
                return PodModelResponse(
                    model_name=model, provider=provider,
                    decision="unknown", confidence=0.0,
                    reasoning=f"Parse error: {parse_err}",
                    raw_output=raw, latency_ms=latency,
                    error=str(parse_err), fallback_used=False,
                )

            return PodModelResponse(
                model_name=model, provider=provider,
                decision=decision, confidence=confidence,
                reasoning=reasoning, raw_output=raw,
                latency_ms=latency, error=None, fallback_used=False,
            )

        except ProviderError as pe:
            latency = int((time.time() - t0) * 1000)
            logger.warning("[%s] %s call failed: %s", self.POD_NAME, label, pe)

            # Try fallback provider
            fallback_provider = self.ai_config.fallback_provider
            fallback_model = self.ai_config.fallback_model
            if fallback_provider != provider or fallback_model != model:
                try:
                    t1 = time.time()
                    raw = call_model(fallback_provider, fallback_model, messages, self.ai_config)
                    latency = int((time.time() - t0) * 1000)
                    parsed = parse_json_response(raw, self.REQUIRED_KEYS)
                    decision = str(parsed.get("decision", "unknown")).lower()
                    if self.VALID_DECISIONS and decision not in self.VALID_DECISIONS:
                        decision = self.VALID_DECISIONS[-1]
                    confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.5))))
                    reasoning = str(parsed.get("reasoning", ""))
                    return PodModelResponse(
                        model_name=fallback_model, provider=fallback_provider,
                        decision=decision, confidence=confidence,
                        reasoning=reasoning, raw_output=raw,
                        latency_ms=latency, error=None, fallback_used=True,
                    )
                except Exception:
                    pass

            return PodModelResponse(
                model_name=model, provider=provider,
                decision="unknown", confidence=0.0,
                reasoning=f"Provider error: {pe}",
                raw_output="", latency_ms=latency,
                error=str(pe), fallback_used=False,
            )

    def run(self, signal: "SignalPacket") -> PodConsensus:
        """Run both models concurrently and return a PodConsensus."""
        t0 = time.time()
        messages_a, messages_b = self.build_messages(signal)

        with ThreadPoolExecutor(max_workers=2) as executor:
            fut_a = executor.submit(
                self._call_one,
                self.pod_config.model_a, self.pod_config.provider_a,
                messages_a, "model_a",
            )
            fut_b = executor.submit(
                self._call_one,
                self.pod_config.model_b, self.pod_config.provider_b,
                messages_b, "model_b",
            )
            resp_a: PodModelResponse = fut_a.result()
            resp_b: PodModelResponse = fut_b.result()

        latency = int((time.time() - t0) * 1000)

        # Agreement score
        both_ok = resp_a.error is None and resp_b.error is None
        if both_ok and resp_a.decision != "unknown" and resp_b.decision != "unknown":
            agreement = 1.0 if resp_a.decision == resp_b.decision else 0.0
        else:
            agreement = 0.5

        conflict = agreement < 0.5 and both_ok

        # Pod decision
        if resp_a.error and resp_b.error:
            pod_decision = "unknown"
            pod_confidence = 0.0
            status = "failed"
            error_msg = f"Both models failed: A={resp_a.error}; B={resp_b.error}"
        elif resp_a.error:
            pod_decision = resp_b.decision
            pod_confidence = resp_b.confidence * 0.7  # penalize single-model
            status = "partial"
            error_msg = f"Model A failed: {resp_a.error}"
        elif resp_b.error:
            pod_decision = resp_a.decision
            pod_confidence = resp_a.confidence * 0.7
            status = "partial"
            error_msg = f"Model B failed: {resp_b.error}"
        else:
            if resp_a.decision == resp_b.decision:
                pod_decision = resp_a.decision
                pod_confidence = (resp_a.confidence + resp_b.confidence) / 2.0
            else:
                # Conflict — pick the one with higher confidence but lower confidence overall
                if resp_a.confidence >= resp_b.confidence:
                    pod_decision = resp_a.decision
                else:
                    pod_decision = resp_b.decision
                pod_confidence = min(resp_a.confidence, resp_b.confidence) * 0.6
            status = "complete"
            error_msg = None

        return PodConsensus(
            pod_name=self.POD_NAME,
            model_a_name=resp_a.model_name,
            model_b_name=resp_b.model_name,
            model_a_provider=resp_a.provider,
            model_b_provider=resp_b.provider,
            model_a_decision=resp_a.decision,
            model_b_decision=resp_b.decision,
            model_a_confidence=resp_a.confidence,
            model_b_confidence=resp_b.confidence,
            model_a_reasoning=resp_a.reasoning,
            model_b_reasoning=resp_b.reasoning,
            agreement_score=agreement,
            conflict_flag=conflict,
            pod_decision=pod_decision,
            pod_confidence=pod_confidence,
            latency_ms=latency,
            fallback_used=resp_a.fallback_used or resp_b.fallback_used,
            status=status,
            error=error_msg,
        )
