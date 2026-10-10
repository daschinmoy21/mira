"""LLM provider package — factory entry point."""

from __future__ import annotations

from typing import cast

from mira.config import LLMConfig
from mira.llm.base import LLMProviderProtocol

# `fallback_model: codex/<model>` runs the fallback through Codex CLI OAuth
# instead of retrying on the primary's endpoint.
CODEX_FALLBACK_PREFIX = "codex/"


def create_llm(config: LLMConfig) -> LLMProviderProtocol:
    """Create the appropriate LLM provider based on config.provider.

    Returns an instance satisfying LLMProviderProtocol. A ``codex/``-prefixed
    ``fallback_model`` wraps the primary in a cross-provider FallbackLLM.
    """
    fallback = config.fallback_model or ""
    if fallback.startswith(CODEX_FALLBACK_PREFIX):
        from mira.llm.codex_cli import CodexCLIProvider
        from mira.llm.fallback import FallbackLLM

        codex_model = fallback.removeprefix(CODEX_FALLBACK_PREFIX) or "codex-default"
        codex = CodexCLIProvider(
            config.model_copy(
                update={"provider": "codex-cli", "model": codex_model, "fallback_model": None}
            )
        )
        # Providers satisfy the protocol structurally at runtime; mypy's
        # settable-attribute rules for Protocols are stricter than that.
        return cast(
            LLMProviderProtocol,
            FallbackLLM(
                _create_provider(config.model_copy(update={"fallback_model": None})),
                cast(LLMProviderProtocol, codex),
                primary_model=config.model,
                fallback_model=fallback,
            ),
        )
    return _create_provider(config)


def _create_provider(config: LLMConfig) -> LLMProviderProtocol:
    if config.provider == "bedrock":
        from mira.llm.bedrock import BedrockProvider

        return BedrockProvider(config)

    if config.provider in {"codex-cli", "codex_cli", "codex"}:
        from mira.llm.codex_cli import CodexCLIProvider

        return CodexCLIProvider(config)

    if config.api_style == "responses":
        from mira.llm.responses import ResponsesProvider

        return ResponsesProvider(config)

    # Default: OpenAI-compatible endpoint (OpenRouter, vLLM, Ollama, etc.)
    from mira.llm.provider import LLMProvider

    return LLMProvider(config)
