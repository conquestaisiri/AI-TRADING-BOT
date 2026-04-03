"""
AI Configuration

Reads all AI-related environment variables, validates them, and returns
a single AIConfig object. No keys are hardcoded here. All config comes
from Replit Secrets / environment variables.

Provider design:
  - OpenRouter is the default: one API key drives many model IDs
  - Groq is optional: separate key, its own model IDs
  - Ollama-compatible is optional: base URL + optional key

Model assignment:
  - Per-pod model A and model B are configured independently of provider keys
  - Each pod can point any of its models at any supported provider
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _str(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _int(key: str, default: int = 0) -> int:
    raw = os.environ.get(key, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _float(key: str, default: float = 0.0) -> float:
    raw = os.environ.get(key, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


@dataclass
class PodConfig:
    pod_name: str
    model_a: str
    model_b: str
    provider_a: str
    provider_b: str

    @property
    def is_ready(self) -> bool:
        return bool(self.model_a and self.model_b)


@dataclass
class AIConfig:
    # ── Core toggles ──────────────────────────────────────────────────────
    enabled: bool
    mode: str                       # "rule_only" | "ai_assisted" | "ai_required"
    orchestration_mode: str         # "light" | "standard" | "full" | "auto"
    review_required: bool
    require_judge_approval: bool
    allow_rule_only_fallback: bool

    # ── Scoring thresholds ────────────────────────────────────────────────
    min_score_for_review: float
    min_score_for_execution: float

    # ── Retry/timeout ─────────────────────────────────────────────────────
    timeout_seconds: int
    max_retries: int
    retry_backoff_seconds: float

    # ── Provider credentials ──────────────────────────────────────────────
    openrouter_api_key: str
    groq_api_key: str
    ollama_base_url: str
    ollama_api_key: str

    # ── Default provider/model ────────────────────────────────────────────
    default_provider: str
    default_model: str
    fallback_provider: str
    fallback_model: str

    # ── Pod configs ───────────────────────────────────────────────────────
    trend_pod: PodConfig
    structure_pod: PodConfig
    regime_pod: PodConfig
    risk_pod: PodConfig
    execution_pod: PodConfig

    # ── Judge ─────────────────────────────────────────────────────────────
    judge_model: str
    judge_provider: str

    # ── Dashboard ─────────────────────────────────────────────────────────
    dashboard_enable_streaming: bool
    dashboard_refresh_seconds: int
    dashboard_max_log_lines: int
    dashboard_default_symbol: str
    dashboard_default_mode: str

    # ── Computed properties ───────────────────────────────────────────────
    missing_secrets: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_openrouter(self) -> bool:
        return bool(self.openrouter_api_key)

    @property
    def has_groq(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def has_ollama(self) -> bool:
        return bool(self.ollama_base_url)

    @property
    def has_any_provider(self) -> bool:
        return self.has_openrouter or self.has_groq or self.has_ollama

    @property
    def ai_actually_enabled(self) -> bool:
        return self.enabled and self.has_any_provider

    def provider_key(self, provider: str) -> str:
        if provider == "openrouter":
            return self.openrouter_api_key
        if provider == "groq":
            return self.groq_api_key
        if provider == "ollama":
            return self.ollama_api_key
        return ""

    def provider_base_url(self, provider: str) -> str:
        if provider == "openrouter":
            return "https://openrouter.ai/api/v1"
        if provider == "groq":
            return "https://api.groq.com/openai/v1"
        if provider == "ollama":
            return self.ollama_base_url.rstrip("/")
        return ""

    def to_status_dict(self) -> dict:
        """Return a safe status dict (no secrets) for the API."""
        def pod_status(pod: PodConfig) -> dict:
            key_a = self.provider_key(pod.provider_a)
            key_b = self.provider_key(pod.provider_b)
            ready_a = bool(pod.model_a and key_a)
            ready_b = bool(pod.model_b and key_b)
            return {
                "pod_name": pod.pod_name,
                "model_a": pod.model_a,
                "model_b": pod.model_b,
                "provider_a": pod.provider_a,
                "provider_b": pod.provider_b,
                "ready_a": ready_a,
                "ready_b": ready_b,
                "ready": ready_a and ready_b,
            }

        return {
            "ai_enabled": self.enabled,
            "ai_actually_enabled": self.ai_actually_enabled,
            "mode": self.mode,
            "orchestration_mode": self.orchestration_mode,
            "has_openrouter": self.has_openrouter,
            "has_groq": self.has_groq,
            "has_ollama": self.has_ollama,
            "has_any_provider": self.has_any_provider,
            "require_judge_approval": self.require_judge_approval,
            "allow_rule_only_fallback": self.allow_rule_only_fallback,
            "min_score_for_review": self.min_score_for_review,
            "min_score_for_execution": self.min_score_for_execution,
            "default_provider": self.default_provider,
            "default_model": self.default_model,
            "judge_model": self.judge_model,
            "judge_provider": self.judge_provider,
            "pods": {
                "trend": pod_status(self.trend_pod),
                "structure": pod_status(self.structure_pod),
                "regime": pod_status(self.regime_pod),
                "risk": pod_status(self.risk_pod),
                "execution": pod_status(self.execution_pod),
            },
            "missing_secrets": self.missing_secrets,
            "warnings": self.warnings,
        }


def load_ai_config() -> AIConfig:
    """Load AI configuration from environment variables. Never raises."""
    missing: list[str] = []
    warnings: list[str] = []

    openrouter_key = _str("OPENROUTER_API_KEY")
    groq_key = _str("GROQ_API_KEY")
    ollama_url = _str("OLLAMA_BASE_URL")
    ollama_key = _str("OLLAMA_API_KEY")

    enabled = _bool("AI_ENABLED", False)

    if enabled and not (openrouter_key or groq_key or ollama_url):
        missing.append("OPENROUTER_API_KEY (or GROQ_API_KEY / OLLAMA_BASE_URL)")
        warnings.append("AI_ENABLED=true but no provider key found — running in rule-only mode")

    def pod(
        name: str,
        default_ma: str,
        default_mb: str,
        default_pa: str = "openrouter",
        default_pb: str = "openrouter",
    ) -> PodConfig:
        prefix = name.upper() + "_POD"
        return PodConfig(
            pod_name=name,
            model_a=_str(f"{prefix}_MODEL_A", default_ma),
            model_b=_str(f"{prefix}_MODEL_B", default_mb),
            provider_a=_str(f"{prefix}_PROVIDER_A", default_pa),
            provider_b=_str(f"{prefix}_PROVIDER_B", default_pb),
        )

    return AIConfig(
        enabled=enabled,
        mode=_str("AI_MODE", "ai_assisted"),
        orchestration_mode=_str("AI_ORCHESTRATION_MODE", "auto"),
        review_required=_bool("AI_REVIEW_REQUIRED", False),
        require_judge_approval=_bool("AI_REQUIRE_JUDGE_APPROVAL", False),
        allow_rule_only_fallback=_bool("AI_ALLOW_RULE_ONLY_FALLBACK", True),
        min_score_for_review=_float("AI_MIN_SCORE_FOR_REVIEW", 0.50),
        min_score_for_execution=_float("AI_MIN_SCORE_FOR_EXECUTION", 0.65),
        timeout_seconds=_int("AI_TIMEOUT_SECONDS", 15),
        max_retries=_int("AI_MAX_RETRIES", 2),
        retry_backoff_seconds=_float("AI_RETRY_BACKOFF_SECONDS", 1.5),
        openrouter_api_key=openrouter_key,
        groq_api_key=groq_key,
        ollama_base_url=ollama_url,
        ollama_api_key=ollama_key,
        default_provider=_str("AI_DEFAULT_PROVIDER", "openrouter"),
        default_model=_str("AI_DEFAULT_MODEL", "mistralai/mistral-7b-instruct"),
        fallback_provider=_str("AI_FALLBACK_PROVIDER", "openrouter"),
        fallback_model=_str("AI_FALLBACK_MODEL", "meta-llama/llama-3-8b-instruct"),
        trend_pod=pod("trend", "mistralai/mistral-7b-instruct", "meta-llama/llama-3-8b-instruct"),
        structure_pod=pod("structure", "mistralai/mistral-7b-instruct", "google/gemma-2-9b-it"),
        regime_pod=pod("regime", "meta-llama/llama-3-8b-instruct", "mistralai/mistral-7b-instruct"),
        risk_pod=pod("risk", "anthropic/claude-3-haiku", "meta-llama/llama-3-8b-instruct"),
        execution_pod=pod("execution", "mistralai/mistral-7b-instruct", "google/gemma-2-9b-it"),
        judge_model=_str("JUDGE_AGENT_MODEL", "anthropic/claude-3-haiku"),
        judge_provider=_str("JUDGE_AGENT_PROVIDER", "openrouter"),
        dashboard_enable_streaming=_bool("DASHBOARD_ENABLE_STREAMING", True),
        dashboard_refresh_seconds=_int("DASHBOARD_REFRESH_SECONDS", 10),
        dashboard_max_log_lines=_int("DASHBOARD_MAX_LOG_LINES", 200),
        dashboard_default_symbol=_str("DASHBOARD_DEFAULT_SYMBOL", "BTCUSDT"),
        dashboard_default_mode=_str("DASHBOARD_DEFAULT_MODE", "standard"),
        missing_secrets=missing,
        warnings=warnings,
    )


# Singleton loaded once at import time
ai_config: AIConfig = load_ai_config()
