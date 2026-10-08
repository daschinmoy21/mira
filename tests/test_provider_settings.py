"""Provider selection, subscription logins and model pickers in the dashboard."""

from __future__ import annotations

import asyncio
import json
import stat
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from mira.config import LLMConfig
from mira.dashboard import provider_login
from mira.dashboard.model_catalog import active_backend, build_options
from mira.dashboard.models_config import (
    CODEX_DEFAULT_MODEL,
    XAI_DEFAULT_MODEL,
    apply_provider_override,
    llm_config_for,
    model_for_provider,
    resolve_provider_choice,
)
from mira.dashboard.routers import providers as providers_router
from mira.llm import codex_auth, xai_oauth

ADMIN = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=1, is_admin=True)))


class FakeDB:
    def __init__(self, **settings: str) -> None:
        self.settings = dict(settings)

    def get_setting(self, key: str) -> str | None:
        return self.settings.get(key)

    def set_setting(self, key: str, value: str) -> None:
        self.settings[key] = value


@pytest.fixture(autouse=True)
def isolated_homes(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRA_XAI_AUTH_FILE", str(tmp_path / "xai-oauth.json"))
    monkeypatch.setenv("MIRA_CODEX_HOME", str(tmp_path / "codex"))
    for var in ("XAI_API_KEY", "CODEX_HOME", "MIRA_INDEX_DIR"):
        monkeypatch.delenv(var, raising=False)
    provider_login._sessions.clear()
    yield
    for name in list(provider_login._sessions):
        provider_login.cancel(name)


def _fake_codex(tmp_path: Path, *, approve: bool = True, models: dict | None = None) -> str:
    """A stand-in `codex` executable covering `login --device-auth` and `debug models`."""
    catalog = json.dumps(models or {"models": []})
    script = tmp_path / "fake-codex"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            if [ "$1" = "debug" ] && [ "$2" = "models" ]; then
              cat <<'JSON'
            {catalog}
            JSON
              exit 0
            fi
            if [ "$1" = "login" ] && [ "$2" = "--device-auth" ]; then
              printf 'Welcome to Codex [v0.161.0]\\n'
              printf 'Follow these steps to sign in with ChatGPT using device code authorization:\\n\\n'
              printf '1. Open this link in your browser and sign in to your account\\n'
              printf '   \\033[94mhttps://auth.openai.com/codex/device\\033[0m\\n\\n'
              printf '2. Enter this one-time code (expires in 15 minutes)\\n'
              printf '   \\033[94mABCD-12345\\033[0m\\n'
              if [ "{"1" if approve else "0"}" = "1" ]; then
                sleep 0.3
                mkdir -p "$CODEX_HOME"
                echo '{{"auth_mode":"chatgpt","tokens":{{"access_token":"x"}}}}' > "$CODEX_HOME/auth.json"
                printf 'Successfully logged in\\n'
                exit 0
              fi
              sleep 0.3
              printf 'Error logging in with device code: denied\\n'
              exit 1
            fi
            exit 2
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


class TestProviderOverride:
    def test_resolve_ignores_unknown_values(self):
        assert resolve_provider_choice(None) == "default"
        assert resolve_provider_choice("") == "default"
        assert resolve_provider_choice("nonsense") == "default"
        assert resolve_provider_choice("xai") == "xai"

    def test_xai_override_rewires_endpoint(self):
        base = LLMConfig(model="anthropic/claude-sonnet-4-6")
        out = apply_provider_override(base, "xai")
        assert out.base_url == "https://api.x.ai/v1"
        assert out.api_style == "responses"
        assert out.api_key_env == "XAI_API_KEY"
        assert base.base_url == "https://openrouter.ai/api/v1"  # original untouched

    def test_codex_override_keeps_deployment_only_settings(self):
        base = LLMConfig(codex_command="/opt/codex", codex_timeout_seconds=30)
        out = apply_provider_override(base, "codex-cli")
        assert out.provider == "codex-cli"
        assert out.codex_command == "/opt/codex"
        assert out.codex_timeout_seconds == 30

    @pytest.mark.parametrize(
        ("choice", "model", "expected"),
        [
            ("xai", "xai/grok-4.7", "xai/grok-4.7"),
            ("xai", "grok-4.6", "grok-4.6"),
            ("xai", "anthropic/claude-sonnet-4-6", XAI_DEFAULT_MODEL),
            ("codex-cli", "gpt-5.6-sol", "gpt-5.6-sol"),
            ("codex-cli", "openai/gpt-5.1-codex", CODEX_DEFAULT_MODEL),
            ("default", "anthropic/claude-sonnet-4-6", "anthropic/claude-sonnet-4-6"),
        ],
    )
    def test_model_for_provider(self, choice, model, expected):
        assert model_for_provider(choice, model) == expected

    def test_llm_config_for_applies_stored_choice(self):
        db = FakeDB(llm_provider="xai", review_model="anthropic/claude-sonnet-4-6")
        with patch("mira.dashboard.api._app_db", db):
            review = llm_config_for("review", LLMConfig(model="m"))
            other = llm_config_for("misc", LLMConfig(model="m"))
        assert review.base_url == "https://api.x.ai/v1"
        # An OpenRouter id left over from before the switch must not be sent to xAI.
        assert review.model == XAI_DEFAULT_MODEL
        assert other.base_url == "https://api.x.ai/v1"

    def test_no_stored_choice_changes_nothing(self):
        with patch("mira.dashboard.api._app_db", FakeDB()):
            cfg = llm_config_for("review", LLMConfig(model="m"))
        assert cfg.base_url == "https://openrouter.ai/api/v1"
        assert cfg.model == "m"


class TestCatalogs:
    def test_xai_backend_detected(self):
        assert active_backend(LLMConfig(base_url="https://api.x.ai/v1")) == "xai"

    def test_xai_options_have_metadata_and_recommendation(self):
        review = build_options("xai", None, "review")
        by_id = {o["value"]: o for o in review}
        assert all(v.startswith("xai/") for v in by_id)
        grok = by_id["xai/grok-4.5"]
        assert grok["recommended"] is True
        assert grok["context_window"] == 500_000
        assert grok["input_cost_per_1m"] == 2.0
        assert grok["reasoning"] is True
        assert review[0]["value"] == "xai/grok-4.5"  # recommended sorts first
        assert [o["value"] for o in build_options("xai", None, "indexing") if o["recommended"]] == [
            "xai/grok-4.3"
        ]

    def test_xai_live_models_merge_without_duplicates(self):
        dynamic = [
            {"value": "xai/grok-4.5", "label": "grok-4.5"},
            {"value": "xai/grok-5-preview", "label": "grok-5-preview"},
        ]
        values = [o["value"] for o in build_options("xai", dynamic, "review")]
        assert values.count("xai/grok-4.5") == 1
        assert "xai/grok-5-preview" in values

    def test_other_backends_do_not_offer_xai_ids(self):
        for backend in ("openrouter", "bedrock", "codex-cli"):
            assert not [o for o in build_options(backend, None, "review") if "xai/" in o["value"]]

    def test_live_provider_prices_are_not_exposed(self):
        dynamic = [{"value": "v/m", "label": "M", "input_cost_per_1m": 9.0, "context_window": 10}]
        option = next(
            o for o in build_options("openrouter", dynamic, "review") if o["value"] == "v/m"
        )
        assert option["context_window"] == 10
        assert "input_cost_per_1m" not in option

    def test_codex_options_keep_cli_order(self):
        dynamic = [
            {"value": "zeta", "label": "Zeta", "reasoning": True},
            {"value": "alpha", "label": "Alpha"},
        ]
        values = [o["value"] for o in build_options("codex-cli", dynamic, "review")]
        assert values == [CODEX_DEFAULT_MODEL, "zeta", "alpha"]


class TestCodexAuth:
    def test_parse_device_prompt_strips_ansi_and_ignores_banner(self):
        text = (
            "Welcome to Codex [v0.161.0]\n"
            "1. Open this link\n   \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n"
            "2. Enter this one-time code\n   \x1b[94mAB12-CD345\x1b[0m\n"
        )
        prompt = codex_auth.parse_device_prompt(text)
        assert prompt.user_code == "AB12-CD345"
        assert prompt.verification_url == "https://auth.openai.com/codex/device"

    def test_parse_device_prompt_needs_both_parts(self):
        assert codex_auth.parse_device_prompt("https://auth.openai.com/codex/device\n") is None
        assert codex_auth.parse_device_prompt("code AB12-CD345") is None

    def test_parse_model_catalog_lists_only_public_models(self):
        raw = json.dumps(
            {
                "models": [
                    {
                        "slug": "gpt-6-sol",
                        "display_name": "GPT-6-Sol",
                        "visibility": "list",
                        "description": "Workhorse",
                        "context_window": 272000,
                        "supported_reasoning_levels": [{"effort": "low"}],
                    },
                    {"slug": "internal", "visibility": "hide"},
                ]
            }
        )
        assert codex_auth.parse_model_catalog(raw) == [
            {
                "value": "gpt-6-sol",
                "label": "GPT-6-Sol",
                "reasoning": True,
                "description": "Workhorse",
                "context_window": 272000,
            }
        ]

    def test_home_resolution_precedence(self, tmp_path, monkeypatch):
        config = LLMConfig()
        assert codex_auth.resolve_home(config) is None  # nothing logged in yet
        home = tmp_path / "codex"
        home.mkdir()
        (home / "auth.json").write_text('{"tokens": {}}')
        assert codex_auth.resolve_home(config) == home
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "env"))
        assert codex_auth.resolve_home(config) == tmp_path / "env"
        explicit = LLMConfig(codex_home=str(tmp_path / "cfg"))
        assert codex_auth.resolve_home(explicit) == tmp_path / "cfg"

    async def test_fetch_models_runs_cli(self, tmp_path):
        models = {"models": [{"slug": "gpt-x", "display_name": "GPT X", "visibility": "list"}]}
        config = LLMConfig(codex_command=_fake_codex(tmp_path, models=models))
        assert [m["value"] for m in await codex_auth.fetch_models(config)] == ["gpt-x"]

    async def test_device_login_end_to_end(self, tmp_path):
        config = LLMConfig(codex_command=_fake_codex(tmp_path))
        started = await provider_login.start("codex-cli", config)
        assert started["state"] == "pending"
        assert started["user_code"] == "ABCD-12345"
        assert started["verification_url"] == "https://auth.openai.com/codex/device"
        for _ in range(50):
            if provider_login.snapshot("codex-cli")["state"] != "pending":
                break
            await asyncio.sleep(0.1)
        assert provider_login.snapshot("codex-cli") == {"state": "complete"}
        assert codex_auth.is_logged_in(codex_auth.login_home(config))
        assert codex_auth.logout(config) is True
        assert not codex_auth.is_logged_in(codex_auth.login_home(config))

    async def test_denied_login_reports_failure(self, tmp_path):
        config = LLMConfig(codex_command=_fake_codex(tmp_path, approve=False))
        await provider_login.start("codex-cli", config)
        for _ in range(50):
            if provider_login.snapshot("codex-cli")["state"] != "pending":
                break
            await asyncio.sleep(0.1)
        state = provider_login.snapshot("codex-cli")
        assert state["state"] == "failed"
        assert "denied" in state["error"]
        assert not codex_auth.is_logged_in(codex_auth.login_home(config))

    async def test_missing_binary_is_a_clear_error(self):
        config = LLMConfig(codex_command="definitely-not-installed-codex")
        with pytest.raises(codex_auth.CodexLoginError, match="not found"):
            await provider_login.start("codex-cli", config)


class TestXaiLoginSession:
    async def test_login_completes_and_saves_credential(self):
        device = xai_oauth.DeviceCode(
            device_code="d",
            user_code="XXXX-YYYY",
            verification_uri="https://accounts.x.ai/oauth2/device",
            verification_uri_complete="https://accounts.x.ai/oauth2/device?user_code=XXXX-YYYY",
            interval=1,
            expires_in=60,
        )
        credential = {"type": "oauth", "access": "a", "refresh": "r", "expires": 1}
        with (
            patch.object(xai_oauth, "request_device_code", return_value=device),
            patch.object(xai_oauth, "poll_for_tokens", return_value=credential),
        ):
            started = await provider_login.start("xai", LLMConfig())
            assert started["user_code"] == "XXXX-YYYY"
            assert started["verification_url"].endswith("user_code=XXXX-YYYY")
            for _ in range(50):
                if provider_login.snapshot("xai")["state"] != "pending":
                    break
                await asyncio.sleep(0.05)
        assert provider_login.snapshot("xai") == {"state": "complete"}
        assert xai_oauth.load_credential()["access"] == "a"

    async def test_denial_is_reported(self):
        device = xai_oauth.DeviceCode("d", "XXXX-YYYY", "https://a.x.ai/d", None, 1, 60)
        with (
            patch.object(xai_oauth, "request_device_code", return_value=device),
            patch.object(
                xai_oauth,
                "poll_for_tokens",
                side_effect=xai_oauth.XaiLoginError("xAI device authorization was denied"),
            ),
        ):
            await provider_login.start("xai", LLMConfig())
            for _ in range(50):
                if provider_login.snapshot("xai")["state"] != "pending":
                    break
                await asyncio.sleep(0.05)
        state = provider_login.snapshot("xai")
        assert state["state"] == "failed"
        assert "denied" in state["error"]

    def test_idle_when_no_session(self):
        assert provider_login.snapshot("xai") == {"state": "idle"}


class TestProviderRoutes:
    def _db(self, **settings):
        return patch("mira.dashboard.api._app_db", FakeDB(**settings))

    def _config(self, **kw):
        return patch.object(
            providers_router, "load_config", return_value=SimpleNamespace(llm=LLMConfig(**kw))
        )

    def test_listing_reflects_login_state(self, tmp_path):
        xai_oauth.save_credential({"access": "a", "refresh": "r", "expires": 1})
        with self._db(), self._config(codex_command=_fake_codex(tmp_path)):
            resp = providers_router.get_provider()
        by_id = {p.value: p for p in resp.providers}
        assert resp.active == "default"
        assert by_id["default"].label.startswith("Deployment default (OpenRouter)")
        assert by_id["xai"].logged_in and by_id["xai"].available
        assert by_id["codex-cli"].available and not by_id["codex-cli"].logged_in

    def test_codex_unavailable_without_binary(self):
        with self._db(), self._config(codex_command="no-such-codex-binary"):
            resp = providers_router.get_provider()
        codex = next(p for p in resp.providers if p.value == "codex-cli")
        assert not codex.available
        assert "not found" in codex.detail

    def test_cannot_select_provider_without_login(self):
        db = FakeDB()
        with (
            patch("mira.dashboard.api._app_db", db),
            self._config(),
            pytest.raises(HTTPException) as exc,
        ):
            providers_router.set_provider(providers_router.ProviderUpdate(provider="xai"), ADMIN)
        assert exc.value.status_code == 400
        assert "Log in" in exc.value.detail
        assert "llm_provider" not in db.settings

    def test_rejects_unknown_provider(self):
        with self._db(), self._config(), pytest.raises(HTTPException) as exc:
            providers_router.set_provider(providers_router.ProviderUpdate(provider="x"), ADMIN)
        assert exc.value.status_code == 400

    def test_select_after_login_then_back_to_default(self):
        xai_oauth.save_credential({"access": "a", "refresh": "r", "expires": 1})
        db = FakeDB()
        with patch("mira.dashboard.api._app_db", db), self._config():
            providers_router.set_provider(providers_router.ProviderUpdate(provider="xai"), ADMIN)
            assert db.settings["llm_provider"] == "xai"
            providers_router.set_provider(
                providers_router.ProviderUpdate(provider="default"), ADMIN
            )
        assert db.settings["llm_provider"] == ""

    def test_logout_clears_credential_and_active_provider(self):
        xai_oauth.save_credential({"access": "a", "refresh": "r", "expires": 1})
        db = FakeDB(llm_provider="xai")
        with patch("mira.dashboard.api._app_db", db), self._config():
            providers_router.logout("xai", ADMIN)
        assert not xai_oauth.stored_login_exists()
        assert db.settings["llm_provider"] == ""

    def test_unknown_provider_path_is_404(self):
        with pytest.raises(HTTPException) as exc:
            providers_router.login_status("openrouter", ADMIN)
        assert exc.value.status_code == 404
