"""Tests for the cross-provider (primary → Codex CLI OAuth) fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from mira.config import LLMConfig
from mira.exceptions import LLMError, NonRetriableLLMError
from mira.llm import create_llm
from mira.llm.codex_cli import CodexCLIProvider
from mira.llm.fallback import FallbackLLM
from mira.llm.provider import LLMProvider
from mira.llm.responses import ResponsesProvider


def _xai_config(**kw) -> LLMConfig:
    return LLMConfig(
        model="xai/grok-4.5",
        base_url="https://api.x.ai/v1",
        api_key_env="XAI_API_KEY",
        api_style="responses",
        **kw,
    )


def test_codex_prefixed_fallback_wraps_primary() -> None:
    llm = create_llm(_xai_config(fallback_model="codex/gpt-6-luna"))

    assert isinstance(llm, FallbackLLM)
    assert isinstance(llm.primary, ResponsesProvider)
    assert llm.primary.config.model == "xai/grok-4.5"
    # The primary must not also retry "codex/…" on the xAI endpoint.
    assert llm.primary.config.fallback_model is None
    assert isinstance(llm.fallback, CodexCLIProvider)
    assert llm.fallback.config.model == "gpt-6-luna"
    assert llm.fallback.config.fallback_model is None
    assert llm.fallback._command("/tmp/out")[-3:] == ["-m", "gpt-6-luna", "-"]


def test_unprefixed_fallback_stays_on_primary_endpoint() -> None:
    """`openai/gpt-…` is an OpenRouter id, not a Codex one — no rerouting."""
    llm = create_llm(LLMConfig(model="anthropic/claude", fallback_model="openai/gpt-4o"))

    assert isinstance(llm, LLMProvider)
    assert llm.config.fallback_model == "openai/gpt-4o"


def _mock_provider(**methods) -> MagicMock:
    provider = MagicMock()
    provider.supports_json_mode = True
    provider.supports_tool_calling = True
    provider.usage = {}
    for name, value in methods.items():
        setattr(provider, name, value)
    return provider


def _wrap(primary: MagicMock, fallback: MagicMock) -> FallbackLLM:
    return FallbackLLM(
        primary, fallback, primary_model="xai/grok-4.5", fallback_model="codex/gpt-6-luna"
    )


@pytest.mark.parametrize(
    "error",
    [TimeoutError("read timeout"), NonRetriableLLMError("api_error", status=401, body="expired")],
)
async def test_primary_failure_switches_to_fallback_for_the_rest_of_the_review(
    error: Exception,
) -> None:
    primary = _mock_provider(review=AsyncMock(side_effect=error), walkthrough=AsyncMock())
    fallback = _mock_provider(
        review=AsyncMock(return_value='{"comments": []}'),
        walkthrough=AsyncMock(return_value='{"summary": "s"}'),
    )
    llm = _wrap(primary, fallback)

    assert await llm.review([{"role": "user", "content": "x"}]) == '{"comments": []}'
    assert await llm.walkthrough([{"role": "user", "content": "x"}]) == '{"summary": "s"}'

    # Sticky: once the primary failed, later calls skip it entirely.
    primary.walkthrough.assert_not_awaited()
    assert llm.usage["model"] == "codex/gpt-6-luna"


async def test_primary_success_never_touches_fallback() -> None:
    primary = _mock_provider(complete=AsyncMock(return_value="ok"))
    fallback = _mock_provider(complete=AsyncMock())
    llm = _wrap(primary, fallback)

    assert await llm.complete([], json_mode=False) == "ok"
    fallback.complete.assert_not_awaited()
    assert llm.usage["model"] == "xai/grok-4.5"


async def test_both_failing_raises_safe_both_models_failed() -> None:
    primary = _mock_provider(complete_with_tools=AsyncMock(side_effect=TimeoutError()))
    fallback = _mock_provider(
        complete_with_tools=AsyncMock(side_effect=LLMError("codex_timeout", seconds=900))
    )
    llm = _wrap(primary, fallback)

    with pytest.raises(LLMError) as exc_info:
        await llm.complete_with_tools([], tools=[{"type": "function"}])

    assert exc_info.value.code == "both_models_failed"
    assert exc_info.value.safe_message == "Both primary and fallback models failed"
    assert "codex/gpt-6-luna" in str(exc_info.value)


def test_usage_sums_both_backends() -> None:
    primary = _mock_provider()
    primary.usage = {"prompt_tokens": 100, "completion_tokens": 10, "cost_usd": 0.5, "model": "x"}
    fallback = _mock_provider()
    fallback.usage = {"prompt_tokens": 40, "completion_tokens": 4, "total_tokens": 44}
    llm = _wrap(primary, fallback)

    usage = llm.usage
    assert usage["prompt_tokens"] == 140
    assert usage["completion_tokens"] == 14
    assert usage["cost_usd"] == 0.5
    assert usage["total_tokens"] == 44
