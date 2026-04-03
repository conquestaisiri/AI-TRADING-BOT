"""
LLM Provider — unified interface for OpenRouter, Groq, and Ollama-compatible endpoints.

Design:
  - One API key per provider (not per model)
  - Model IDs are configured independently per pod/agent
  - All providers expose an OpenAI-compatible chat completions interface
  - Ollama uses its own /api/chat interface but the adapter normalizes the output

Retry logic: exponential backoff, configurable per AIConfig.
Fallback: if primary provider fails, can optionally fall back to a secondary.
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai.config import AIConfig

from logs.logger import get_logger

logger = get_logger("ai.provider")


class ProviderError(Exception):
    """Raised when a provider call fails after all retries."""
    pass


def _openai_compatible_call(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    timeout: int,
    extra_headers: dict | None = None,
) -> dict:
    """
    Call an OpenAI-compatible /chat/completions endpoint.
    Returns the raw JSON response dict.
    """
    url = f"{base_url}/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": 256,
        "temperature": 0.2,
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if extra_headers:
        headers.update(extra_headers)

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _ollama_call(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    timeout: int,
) -> dict:
    """
    Call an Ollama-compatible /api/chat endpoint.
    Normalizes response to OpenAI format.
    """
    url = f"{base_url}/api/chat"
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "stream": False,
    }).encode("utf-8")

    headers: dict = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = json.loads(resp.read().decode("utf-8"))

    # Normalize to OpenAI format
    content = raw.get("message", {}).get("content", "")
    return {
        "choices": [{"message": {"content": content}}]
    }


def call_model(
    provider: str,
    model: str,
    messages: list[dict],
    config: "AIConfig",
) -> str:
    """
    Call a single model via its provider.
    Returns the assistant message content string.
    Raises ProviderError on failure after all retries.
    """
    base_url = config.provider_base_url(provider)
    api_key = config.provider_key(provider)

    if not base_url:
        raise ProviderError(f"Provider '{provider}' is not configured (no base URL)")
    if provider not in ("ollama",) and not api_key:
        raise ProviderError(f"Provider '{provider}' has no API key configured")

    extra_headers: dict = {}
    if provider == "openrouter":
        extra_headers["HTTP-Referer"] = "https://neuraltrader.replit.app"
        extra_headers["X-Title"] = "NeuralTrader"

    last_err: Exception = Exception("No attempts made")
    for attempt in range(config.max_retries + 1):
        try:
            t0 = time.time()

            if provider == "ollama":
                raw = _ollama_call(base_url, api_key, model, messages, config.timeout_seconds)
            else:
                raw = _openai_compatible_call(
                    base_url, api_key, model, messages,
                    config.timeout_seconds, extra_headers if extra_headers else None
                )

            elapsed = int((time.time() - t0) * 1000)
            content = raw["choices"][0]["message"]["content"].strip()
            logger.debug("Provider %s model %s responded in %dms", provider, model, elapsed)
            return content

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200]
            last_err = ProviderError(f"HTTP {e.code}: {body}")
            logger.warning("Provider %s attempt %d/%d failed: HTTP %d — %s",
                           provider, attempt + 1, config.max_retries + 1, e.code, body[:80])
        except urllib.error.URLError as e:
            last_err = ProviderError(f"Network error: {e.reason}")
            logger.warning("Provider %s attempt %d/%d failed: %s",
                           provider, attempt + 1, config.max_retries + 1, e.reason)
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            last_err = ProviderError(f"Parse error: {e}")
            logger.warning("Provider %s attempt %d/%d parse error: %s",
                           provider, attempt + 1, config.max_retries + 1, e)
        except Exception as e:
            last_err = ProviderError(str(e))
            logger.warning("Provider %s attempt %d/%d unexpected error: %s",
                           provider, attempt + 1, config.max_retries + 1, e)

        if attempt < config.max_retries:
            backoff = config.retry_backoff_seconds * (2 ** attempt)
            time.sleep(backoff)

    raise last_err


def parse_json_response(raw: str, required_keys: list[str]) -> dict:
    """
    Parse a JSON response from a model, extracting required keys.
    Raises ValueError if required keys are missing.
    """
    text = raw.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    # Find the first { and last } to handle extra prose
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON object found in response: {raw[:100]!r}")

    parsed = json.loads(text[start:end])

    missing = [k for k in required_keys if k not in parsed]
    if missing:
        raise ValueError(f"Missing required keys {missing} in response: {parsed}")

    return parsed
