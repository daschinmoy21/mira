"""Token and cost accounting helpers for LLM responses.

OpenRouter (and other OpenAI-compatible providers) return a ``usage`` object
with prompt/completion counts, optional cache details, and a billed ``cost``.
This module normalizes those shapes and estimates USD when the provider does
not report cost (Bedrock, local endpoints, etc.).
"""

from __future__ import annotations

from typing import Any

from mira.llm import registry

# Anthropic-style cache multipliers used only when the provider omits cost.
# Cache *reads* are typically ~10% of input price; *writes* ~125%.
_CACHE_READ_MULTIPLIER = 0.10
_CACHE_WRITE_MULTIPLIER = 1.25


def estimate_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Estimate USD cost from token counts and registry pricing.

    ``prompt_tokens`` is treated as total input (including cached tokens),
    matching OpenRouter's native-tokenizer accounting. Non-cached input is
    charged at the full input rate; cached reads/writes use Anthropic-like
    multipliers when the registry has no cache-specific fields.
    """
    input_per_m, output_per_m = registry.pricing(model)
    info = registry.get(model) or {}
    cache_read_per_m = float(
        info.get("cache_read_cost_per_1m", input_per_m * _CACHE_READ_MULTIPLIER)
    )
    cache_write_per_m = float(
        info.get("cache_write_cost_per_1m", input_per_m * _CACHE_WRITE_MULTIPLIER)
    )

    cached = max(0, int(cached_tokens))
    cache_write = max(0, int(cache_write_tokens))
    billable_input = max(0, int(prompt_tokens) - cached)

    return (
        (billable_input / 1_000_000) * input_per_m
        + (cached / 1_000_000) * cache_read_per_m
        + (cache_write / 1_000_000) * cache_write_per_m
        + (int(completion_tokens) / 1_000_000) * output_per_m
    )


def parse_openai_usage(usage: dict[str, Any] | None) -> dict[str, int | float]:
    """Normalize an OpenAI/OpenRouter-style ``usage`` object.

    Returns keys: prompt_tokens, completion_tokens, cached_tokens,
    cache_write_tokens, reasoning_tokens, and optionally cost_usd when the
    provider included a ``cost`` field (OpenRouter). Missing fields are 0;
    ``cost_usd`` is omitted when the provider did not report cost so callers
    can decide whether to estimate.
    """
    if not usage:
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": 0,
        }

    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)

    prompt_details = usage.get("prompt_tokens_details") or {}
    if not isinstance(prompt_details, dict):
        prompt_details = {}
    cached = int(prompt_details.get("cached_tokens") or 0)
    cache_write = int(prompt_details.get("cache_write_tokens") or 0)

    completion_details = usage.get("completion_tokens_details") or {}
    if not isinstance(completion_details, dict):
        completion_details = {}
    reasoning = int(completion_details.get("reasoning_tokens") or 0)

    out: dict[str, int | float] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_tokens": cached,
        "cache_write_tokens": cache_write,
        "reasoning_tokens": reasoning,
    }
    # OpenRouter always includes cost when accounting is on (default). A
    # present key — even 0 for free models — is authoritative billed USD.
    if "cost" in usage and usage["cost"] is not None:
        out["cost_usd"] = float(usage["cost"])
    return out


def parse_bedrock_usage(usage: dict[str, Any] | None) -> dict[str, int | float]:
    """Normalize a Bedrock Converse ``usage`` object (camelCase fields)."""
    if not usage:
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": 0,
        }
    return {
        "prompt_tokens": int(usage.get("inputTokens") or 0),
        "completion_tokens": int(usage.get("outputTokens") or 0),
        "cached_tokens": int(usage.get("cacheReadInputTokens") or 0),
        "cache_write_tokens": int(usage.get("cacheWriteInputTokens") or 0),
        "reasoning_tokens": 0,
    }
