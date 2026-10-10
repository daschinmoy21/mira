"""Cross-provider fallback — run a primary provider, fall back to another backend.

The built-in ``fallback_model`` retry swaps the model id on the *same* endpoint,
which can't express "xAI Grok via OAuth, falling back to Codex via OAuth".
A ``fallback_model`` of ``codex/<model>`` instead wraps the primary in
:class:`FallbackLLM`, whose fallback is a :class:`CodexCLIProvider`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from mira.exceptions import LLMError
from mira.llm.base import LLMProviderProtocol

logger = logging.getLogger(__name__)

T = TypeVar("T")


class FallbackLLM:
    """Primary provider with a fallback on a different backend.

    Any primary failure — including 4xx, since an expired OAuth login is
    exactly when another backend helps — switches to the fallback. The switch
    is sticky for this instance (one review): after a primary timeout, every
    remaining call would otherwise wait out the same timeout and retries first.
    """

    def __init__(
        self,
        primary: LLMProviderProtocol,
        fallback: LLMProviderProtocol,
        *,
        primary_model: str,
        fallback_model: str,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self._primary_failed = False

    # Config and capabilities follow whichever backend is serving calls now.

    @property
    def _active(self) -> LLMProviderProtocol:
        return self.fallback if self._primary_failed else self.primary

    @property
    def config(self) -> Any:
        return self._active.config  # type: ignore[attr-defined]

    @property
    def supports_json_mode(self) -> bool:
        return bool(self._active.supports_json_mode)

    @property
    def supports_tool_calling(self) -> bool:
        return bool(self._active.supports_tool_calling)

    @property
    def supports_temperature(self) -> bool:
        return bool(getattr(self._active, "supports_temperature", True))

    async def _call(self, call: Callable[[LLMProviderProtocol], Awaitable[T]]) -> T:
        if not self._primary_failed:
            try:
                return await call(self.primary)
            except Exception as primary_err:
                self._primary_failed = True
                logger.warning(
                    "Primary model %s failed (%s), switching to fallback %s",
                    self.primary_model,
                    primary_err,
                    self.fallback_model,
                )
        try:
            return await call(self.fallback)
        except Exception as fallback_err:
            raise LLMError(
                "both_models_failed",
                primary_model=self.primary_model,
                fallback_model=self.fallback_model,
                error=fallback_err,
            ) from fallback_err

    async def complete(
        self,
        messages: list[dict[str, str]],
        json_mode: bool = True,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        return await self._call(
            lambda llm: llm.complete(
                messages, json_mode=json_mode, temperature=temperature, max_tokens=max_tokens
            )
        )

    async def complete_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict],
        temperature: float | None = None,
    ) -> str:
        return await self._call(
            lambda llm: llm.complete_with_tools(messages, tools, temperature=temperature)
        )

    async def complete_agentic(
        self,
        messages: list,
        tools: list[dict],
        temperature: float | None = None,
    ) -> dict:
        return await self._call(
            lambda llm: llm.complete_agentic(messages, tools, temperature=temperature)
        )

    async def review(self, messages: list[dict[str, str]], temperature: float | None = None) -> str:
        return await self._call(lambda llm: llm.review(messages, temperature=temperature))

    async def walkthrough(self, messages: list[dict[str, str]]) -> str:
        return await self._call(lambda llm: llm.walkthrough(messages))

    def count_tokens(self, text: str) -> int:
        return self._active.count_tokens(text)

    def _total(self, attr: str) -> Any:
        return getattr(self.primary, attr, 0) + getattr(self.fallback, attr, 0)

    @property
    def total_prompt_tokens(self) -> int:
        return int(self._total("total_prompt_tokens"))

    @property
    def total_completion_tokens(self) -> int:
        return int(self._total("total_completion_tokens"))

    @property
    def total_cached_tokens(self) -> int:
        return int(self._total("total_cached_tokens"))

    @property
    def total_cache_write_tokens(self) -> int:
        return int(self._total("total_cache_write_tokens"))

    @property
    def total_cost_usd(self) -> float:
        return float(self._total("total_cost_usd"))

    @property
    def usage(self) -> dict[str, int | float | str]:
        """Token totals summed across both backends. ``model`` names the
        fallback once it took over, so the dashboard attributes the review to
        the model that actually produced it."""
        combined: dict[str, int | float | str] = {}
        for part in (self.primary.usage, self.fallback.usage):
            for key, value in part.items():
                if isinstance(value, int | float) and not isinstance(value, bool):
                    prev = combined.get(key, 0)
                    combined[key] = (prev if isinstance(prev, int | float) else 0) + value
        combined["model"] = self.fallback_model if self._primary_failed else self.primary_model
        return combined
