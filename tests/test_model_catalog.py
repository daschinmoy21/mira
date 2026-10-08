"""Tests for the backend-aware dynamic model catalog."""

from __future__ import annotations

import pytest

from mira.config import LLMConfig
from mira.dashboard import model_catalog
from mira.dashboard.model_catalog import active_backend, build_options


@pytest.fixture(autouse=True)
def _clear_cache():
    model_catalog._cache.clear()
    yield
    model_catalog._cache.clear()


class TestActiveBackend:
    def test_default_is_openrouter(self):
        assert active_backend(LLMConfig()) == "openrouter"

    def test_bedrock_provider(self):
        assert active_backend(LLMConfig(provider="bedrock")) == "bedrock"

    def test_codex_cli_provider(self):
        assert active_backend(LLMConfig(provider="codex-cli")) == "codex-cli"

    def test_generic_endpoint(self):
        assert (
            active_backend(LLMConfig(base_url="http://localhost:11434/v1")) == "openai-compatible"
        )


class TestBuildOptions:
    def test_registry_filtered_by_backend(self):
        openrouter = [m["value"] for m in build_options("openrouter", None, "review")]
        bedrock = [m["value"] for m in build_options("bedrock", None, "review")]
        assert "anthropic/claude-sonnet-4-6" in openrouter
        assert "us.anthropic.claude-sonnet-4-6-v1:0" not in openrouter
        assert "us.anthropic.claude-sonnet-4-6-v1:0" in bedrock
        assert "anthropic/claude-sonnet-4-6" not in bedrock

    def test_codex_backend_only_offers_codex_models(self):
        values = [m["value"] for m in build_options("codex-cli", None, "review")]
        assert values == ["codex-default"]

    def test_openrouter_does_not_offer_codex_models(self):
        values = [m["value"] for m in build_options("openrouter", None, "review")]
        assert "codex-default" not in values

    def test_dynamic_merged_and_deduped_against_registry(self):
        dynamic = [
            {"value": "anthropic/claude-sonnet-4.6", "label": "Anthropic: Claude Sonnet 4.6"},
            {"value": "mistralai/mistral-large-3", "label": "Mistral Large 3"},
        ]
        options = build_options("openrouter", dynamic, "review")
        values = [m["value"] for m in options]
        # Dot-form alias of a registry id is dropped; genuinely new model kept.
        assert "anthropic/claude-sonnet-4.6" not in values
        assert "anthropic/claude-sonnet-4-6" in values
        assert "mistralai/mistral-large-3" in values

    def test_generic_endpoint_uses_dynamic_only(self):
        dynamic = [{"value": "llama-3.3-70b", "label": "llama-3.3-70b"}]
        values = [m["value"] for m in build_options("openai-compatible", dynamic, "review")]
        assert values == ["llama-3.3-70b"]

    def test_generic_endpoint_falls_back_to_registry(self):
        values = [m["value"] for m in build_options("openai-compatible", None, "review")]
        assert "anthropic/claude-sonnet-4-6" in values

    def test_recommended_sort_first(self):
        options = build_options("openrouter", None, "indexing")
        assert options[0]["recommended"] is True


class TestFetchCatalog:
    @pytest.mark.asyncio
    async def test_openrouter_pricing_is_normalized(self, monkeypatch):
        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "data": [
                        {
                            "id": "example/model",
                            "name": "Example",
                            "supported_parameters": ["tools"],
                            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                        }
                    ]
                }

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, *args, **kwargs):
                return Response()

        monkeypatch.setattr(model_catalog.httpx, "AsyncClient", lambda **kwargs: Client())
        result = await model_catalog._fetch_openai_style(LLMConfig(), tools_only=True)
        assert result[0]["input_cost_per_1m"] == pytest.approx(1.0)
        assert result[0]["output_cost_per_1m"] == pytest.approx(2.0)
