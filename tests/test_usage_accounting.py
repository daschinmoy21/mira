"""Token + cost accounting (OpenRouter usage shapes and cost estimates)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mira.config import LLMConfig
from mira.index.store import IndexStore
from mira.llm.provider import LLMProvider
from mira.llm.usage import estimate_cost_usd, parse_bedrock_usage, parse_openai_usage

os.environ.setdefault("OPENROUTER_API_KEY", "test-key-for-unit-tests")


class TestParseOpenAIUsage:
    def test_empty(self):
        assert parse_openai_usage(None)["prompt_tokens"] == 0
        assert "cost_usd" not in parse_openai_usage({})

    def test_openrouter_full_shape(self):
        parsed = parse_openai_usage(
            {
                "prompt_tokens": 1000,
                "completion_tokens": 200,
                "total_tokens": 1200,
                "cost": 0.0123,
                "prompt_tokens_details": {
                    "cached_tokens": 400,
                    "cache_write_tokens": 50,
                },
                "completion_tokens_details": {"reasoning_tokens": 80},
            }
        )
        assert parsed["prompt_tokens"] == 1000
        assert parsed["completion_tokens"] == 200
        assert parsed["cached_tokens"] == 400
        assert parsed["cache_write_tokens"] == 50
        assert parsed["reasoning_tokens"] == 80
        assert parsed["cost_usd"] == pytest.approx(0.0123)

    def test_cost_zero_is_authoritative(self):
        # Free models report cost: 0 — must not fall through to estimate.
        parsed = parse_openai_usage(
            {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0}
        )
        assert parsed["cost_usd"] == 0.0


class TestParseBedrockUsage:
    def test_camel_case_with_cache(self):
        parsed = parse_bedrock_usage(
            {
                "inputTokens": 500,
                "outputTokens": 100,
                "cacheReadInputTokens": 200,
                "cacheWriteInputTokens": 25,
            }
        )
        assert parsed["prompt_tokens"] == 500
        assert parsed["completion_tokens"] == 100
        assert parsed["cached_tokens"] == 200
        assert parsed["cache_write_tokens"] == 25
        assert "cost_usd" not in parsed


class TestEstimateCost:
    def test_basic_input_output(self):
        # Sonnet fallback: $3/M in, $15/M out
        cost = estimate_cost_usd("unknown/model", 1_000_000, 1_000_000)
        assert cost == pytest.approx(3.0 + 15.0)

    def test_cache_reduces_billable_input(self):
        full = estimate_cost_usd("unknown/model", 1_000_000, 0, cached_tokens=0)
        half_cached = estimate_cost_usd(
            "unknown/model", 1_000_000, 0, cached_tokens=500_000
        )
        assert half_cached < full


class TestProviderUsageTracking:
    @pytest.mark.asyncio
    async def test_records_cache_and_cost_from_openrouter(self):
        config = LLMConfig(model="anthropic/claude-sonnet-4-6")
        provider = LLMProvider(config)

        usage = {
            "prompt_tokens": 1000,
            "completion_tokens": 100,
            "cost": 0.05,
            "prompt_tokens_details": {"cached_tokens": 300, "cache_write_tokens": 20},
            "completion_tokens_details": {"reasoning_tokens": 40},
        }
        resp_data = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": usage,
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = resp_data
        mock_resp.text = "{}"

        with patch("mira.llm.provider.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await provider.complete([{"role": "user", "content": "hi"}], json_mode=False)

        u = provider.usage
        assert u["prompt_tokens"] == 1000
        assert u["completion_tokens"] == 100
        assert u["cached_tokens"] == 300
        assert u["cache_write_tokens"] == 20
        assert u["reasoning_tokens"] == 40
        assert u["cost_usd"] == pytest.approx(0.05)
        assert u["model"] == "anthropic/claude-sonnet-4-6"
        assert u["total_tokens"] == 1100

    @pytest.mark.asyncio
    async def test_estimates_cost_when_provider_omits_it(self):
        config = LLMConfig(
            model="anthropic/claude-sonnet-4-6",
            base_url="http://localhost:11434/v1",
            api_key_env="",
        )
        provider = LLMProvider(config)

        resp_data = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0},
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = resp_data
        mock_resp.text = "{}"

        with patch("mira.llm.provider.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await provider.complete([{"role": "user", "content": "hi"}], json_mode=False)

        # Sonnet-tier pricing for this model id: $3 / 1M input
        assert provider.usage["cost_usd"] == pytest.approx(3.0)


class TestReviewEventPersistence:
    def test_store_roundtrip_usage_fields(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MIRA_INDEX_DIR", str(tmp_path))
        store = IndexStore.open("acme", "web")
        ev = store.record_review(
            pr_number=1,
            pr_title="test",
            pr_url="https://example.com/1",
            comments_posted=1,
            blockers=0,
            warnings=0,
            tokens_used=1200,
            prompt_tokens=1000,
            completion_tokens=200,
            cached_tokens=400,
            cache_write_tokens=50,
            cost_usd=0.042,
            model="anthropic/claude-sonnet-4-6",
        )
        assert ev.cost_usd == pytest.approx(0.042)
        assert ev.prompt_tokens == 1000

        listed = store.list_review_events(limit=1)
        assert len(listed) == 1
        assert listed[0].completion_tokens == 200
        assert listed[0].cached_tokens == 400
        assert listed[0].cache_write_tokens == 50
        assert listed[0].cost_usd == pytest.approx(0.042)
        assert listed[0].model == "anthropic/claude-sonnet-4-6"

        stats = store.get_review_stats()
        assert stats["total_tokens"] == 1200
        assert stats["total_prompt_tokens"] == 1000
        assert stats["total_completion_tokens"] == 200
        assert stats["total_cached_tokens"] == 400
        assert stats["total_cache_write_tokens"] == 50
        assert stats["total_cost_usd"] == pytest.approx(0.042)
        store.close()
