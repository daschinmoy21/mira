"""Dashboard routes for choosing an LLM provider and logging in to it.

``xai`` and ``codex-cli`` are subscription-login providers (SuperGrok / X
Premium, ChatGPT). Selecting one stores a ``llm_provider`` override that
``llm_config_for`` applies on top of the deployment config; logging in is a
device-code flow driven from here (see ``provider_login``).
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel

from mira.config import LLMConfig, load_config
from mira.dashboard import api as _api
from mira.dashboard import provider_login
from mira.dashboard.api import _require_admin, router
from mira.dashboard.model_catalog import active_backend
from mira.dashboard.models_config import (
    PROVIDER_CODEX,
    PROVIDER_DEFAULT,
    PROVIDER_VALUES,
    PROVIDER_XAI,
    resolve_provider_choice,
)
from mira.llm import codex_auth, xai_oauth

_LOGIN_PROVIDERS = (PROVIDER_XAI, PROVIDER_CODEX)
_BACKEND_LABELS = {
    "openrouter": "OpenRouter",
    "bedrock": "AWS Bedrock",
    "codex-cli": "Codex CLI",
    "xai": "xAI",
}


class ProviderInfo(BaseModel):
    value: str
    label: str
    description: str
    requires_login: bool
    logged_in: bool
    available: bool
    detail: str | None = None


class ProviderResponse(BaseModel):
    active: str
    providers: list[ProviderInfo]


class ProviderUpdate(BaseModel):
    provider: str


class LoginState(BaseModel):
    state: str
    user_code: str | None = None
    verification_url: str | None = None
    expires_in: int | None = None
    error: str | None = None


def _default_label(config: LLMConfig) -> str:
    backend = active_backend(config)
    if backend in _BACKEND_LABELS:
        return _BACKEND_LABELS[backend]
    return urlparse(config.base_url).hostname or "custom endpoint"


def _xai_info() -> ProviderInfo:
    if os.environ.get("XAI_API_KEY"):
        logged_in, detail = True, "Using the XAI_API_KEY environment variable"
    elif xai_oauth.stored_login_exists():
        logged_in, detail = True, "Signed in with your xAI account"
    else:
        logged_in, detail = False, "Sign in with SuperGrok or X Premium"
    return ProviderInfo(
        value=PROVIDER_XAI,
        label="xAI Grok",
        description="Grok models via your SuperGrok / X Premium subscription.",
        requires_login=True,
        logged_in=logged_in,
        available=True,
        detail=detail,
    )


def _codex_info(config: LLMConfig) -> ProviderInfo:
    installed = codex_auth.command_path(config) is not None
    logged_in = codex_auth.is_logged_in(codex_auth.login_home(config))
    if not installed:
        detail = (
            "Codex CLI not found on the server (install @openai/codex or set llm.codex_command)"
        )
    elif logged_in:
        detail = "Signed in with your ChatGPT account"
    else:
        detail = "Sign in with your ChatGPT account"
    return ProviderInfo(
        value=PROVIDER_CODEX,
        label="OpenAI Codex",
        description="GPT models via your ChatGPT plan, run through the Codex CLI.",
        requires_login=True,
        logged_in=logged_in,
        available=installed,
        detail=detail,
    )


def _provider_response() -> ProviderResponse:
    config = load_config().llm
    active = resolve_provider_choice(_api._app_db.get_setting("llm_provider"))
    return ProviderResponse(
        active=active,
        providers=[
            ProviderInfo(
                value=PROVIDER_DEFAULT,
                label=f"Deployment default ({_default_label(config)})",
                description="Whatever mira.yaml and the environment configure.",
                requires_login=False,
                logged_in=True,
                available=True,
            ),
            _xai_info(),
            _codex_info(config),
        ],
    )


def _check_login_provider(provider: str) -> None:
    if provider not in _LOGIN_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider {provider!r}")


@router.get("/api/settings/provider", response_model=ProviderResponse)
def get_provider() -> ProviderResponse:
    return _provider_response()


@router.put("/api/settings/provider")
def set_provider(body: ProviderUpdate, request: Request) -> dict:
    _require_admin(request)
    if body.provider not in PROVIDER_VALUES:
        raise HTTPException(status_code=400, detail=f"{body.provider!r} is not a valid provider.")
    if body.provider != PROVIDER_DEFAULT:
        info = next(p for p in _provider_response().providers if p.value == body.provider)
        if not info.available:
            raise HTTPException(status_code=400, detail=info.detail or "Provider unavailable.")
        if not info.logged_in:
            raise HTTPException(status_code=400, detail=f"Log in to {info.label} first.")
    # "" (not None — the column is NOT NULL) reads back as unset.
    _api._app_db.set_setting(
        "llm_provider", "" if body.provider == PROVIDER_DEFAULT else body.provider
    )
    return {"ok": True}


@router.post("/api/settings/provider/{provider}/login", response_model=LoginState)
async def start_login(provider: str, request: Request) -> LoginState:
    _require_admin(request)
    _check_login_provider(provider)
    config = load_config().llm
    try:
        return LoginState(**await provider_login.start(provider, config))
    except (xai_oauth.XaiLoginError, codex_auth.CodexLoginError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not reach the sign-in service: {exc}"
        ) from exc


@router.get("/api/settings/provider/{provider}/login/status", response_model=LoginState)
def login_status(provider: str, request: Request) -> LoginState:
    _require_admin(request)
    _check_login_provider(provider)
    return LoginState(**provider_login.snapshot(provider))


@router.post("/api/settings/provider/{provider}/logout")
def logout(provider: str, request: Request) -> dict:
    _require_admin(request)
    _check_login_provider(provider)
    provider_login.cancel(provider)
    if provider == PROVIDER_XAI:
        xai_oauth.clear_credential()
    else:
        codex_auth.logout(load_config().llm)
    # Don't leave the deployment pointed at a provider we can no longer use.
    if resolve_provider_choice(_api._app_db.get_setting("llm_provider")) == provider:
        _api._app_db.set_setting("llm_provider", "")
    return {"ok": True}
