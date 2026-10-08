"""Tests for the xAI subscription (OAuth device-code) login."""

from __future__ import annotations

import json
import stat
import time
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from mira.cli import main
from mira.config import LLMConfig
from mira.exceptions import LLMError
from mira.llm import xai_oauth
from mira.llm.base import _get_api_key
from mira.llm.responses import ResponsesProvider


def _resp(status: int, body: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    return r


DEVICE_BODY = {
    "device_code": "dev-123",
    "user_code": "ABCD-EFGH",
    "verification_uri": "https://accounts.x.ai/oauth2/device",
    "verification_uri_complete": "https://accounts.x.ai/oauth2/device?user_code=ABCD-EFGH",
    "expires_in": 1800,
    "interval": 5,
}
TOKEN_BODY = {"access_token": "acc-1", "refresh_token": "ref-1", "expires_in": 3600}


@pytest.fixture(autouse=True)
def auth_path(tmp_path, monkeypatch):
    path = tmp_path / "xai-oauth.json"
    monkeypatch.setenv("MIRA_XAI_AUTH_FILE", str(path))
    for var in ("XAI_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return path


def _xai_config(**overrides) -> LLMConfig:
    defaults = {
        "model": "xai/grok-4.5",
        "base_url": "https://api.x.ai/v1",
        "api_style": "responses",
    }
    return LLMConfig(**{**defaults, **overrides})


class TestDeviceFlow:
    def test_request_device_code(self):
        with patch("httpx.post", return_value=_resp(200, DEVICE_BODY)) as post:
            device = xai_oauth.request_device_code()
        assert device.user_code == "ABCD-EFGH"
        assert device.interval == 5
        sent = post.call_args.kwargs["data"]
        assert sent["client_id"] == xai_oauth.CLIENT_ID
        assert "api:access" in sent["scope"]

    def test_rejects_non_https_verification_url(self):
        body = {**DEVICE_BODY, "verification_uri_complete": "file:///etc/passwd"}
        with (
            patch("httpx.post", return_value=_resp(200, body)),
            pytest.raises(xai_oauth.XaiLoginError, match="untrusted"),
        ):
            xai_oauth.request_device_code()

    def test_device_request_failure(self):
        with (
            patch("httpx.post", return_value=_resp(400, {"error": "invalid_client"})),
            pytest.raises(xai_oauth.XaiLoginError, match="invalid_client"),
        ):
            xai_oauth.request_device_code()

    def _device(self) -> xai_oauth.DeviceCode:
        with patch("httpx.post", return_value=_resp(200, DEVICE_BODY)):
            return xai_oauth.request_device_code()

    def test_poll_waits_through_pending_and_slow_down(self):
        device = self._device()
        responses = [
            _resp(400, {"error": "authorization_pending"}),
            _resp(400, {"error": "slow_down"}),
            _resp(200, TOKEN_BODY),
        ]
        sleeps: list[float] = []
        with patch("httpx.post", side_effect=responses):
            cred = xai_oauth.poll_for_tokens(device, sleep=sleeps.append)
        assert cred["access"] == "acc-1"
        assert cred["refresh"] == "ref-1"
        assert cred["expires"] > time.time() * 1000
        # slow_down widens the polling interval
        assert sleeps == [5, 5, 10]

    @pytest.mark.parametrize(
        ("error", "message"),
        [("access_denied", "denied"), ("expired_token", "expired")],
    )
    def test_poll_terminal_errors(self, error, message):
        device = self._device()
        with (
            patch("httpx.post", return_value=_resp(400, {"error": error})),
            pytest.raises(xai_oauth.XaiLoginError, match=message),
        ):
            xai_oauth.poll_for_tokens(device, sleep=lambda _s: None)

    def test_poll_gives_up_after_expiry(self):
        device = self._device()
        clock = iter([0, 0, 2000])
        with (
            patch("httpx.post", return_value=_resp(400, {"error": "authorization_pending"})),
            pytest.raises(xai_oauth.XaiLoginError, match="expired"),
        ):
            xai_oauth.poll_for_tokens(device, sleep=lambda _s: None, monotonic=lambda: next(clock))


class TestStorageAndRefresh:
    def test_credential_file_is_private(self, auth_path):
        xai_oauth.save_credential({"access": "a", "refresh": "r", "expires": 1})
        assert stat.S_IMODE(auth_path.stat().st_mode) == 0o600
        assert xai_oauth.load_credential()["access"] == "a"

    def test_load_ignores_garbage(self, auth_path):
        auth_path.write_text("not json")
        assert xai_oauth.load_credential() is None
        auth_path.write_text(json.dumps({"access": "only-access"}))
        assert xai_oauth.load_credential() is None

    def test_valid_token_is_returned_without_network(self):
        future = int(time.time() * 1000) + 600_000
        xai_oauth.save_credential({"access": "live", "refresh": "r", "expires": future})
        with patch("httpx.post") as post:
            assert xai_oauth.get_access_token() == "live"
        post.assert_not_called()

    def test_expired_token_refreshes_and_persists(self):
        xai_oauth.save_credential({"access": "old", "refresh": "r-old", "expires": 1})
        body = {"access_token": "new", "refresh_token": "r-new", "expires_in": 3600}
        with patch("httpx.post", return_value=_resp(200, body)) as post:
            assert xai_oauth.get_access_token() == "new"
        assert post.call_args.kwargs["data"]["grant_type"] == "refresh_token"
        stored = xai_oauth.load_credential()
        assert stored["access"] == "new"
        assert stored["refresh"] == "r-new"

    def test_refresh_keeps_old_refresh_token_when_not_rotated(self):
        xai_oauth.save_credential({"access": "old", "refresh": "r-keep", "expires": 1})
        with patch("httpx.post", return_value=_resp(200, {"access_token": "new"})):
            xai_oauth.get_access_token()
        assert xai_oauth.load_credential()["refresh"] == "r-keep"

    def test_no_login_raises_actionable_error(self):
        with pytest.raises(LLMError) as exc:
            xai_oauth.get_access_token()
        assert "mira login xai" in str(exc.value)
        assert exc.value.safe_message == "xAI login required"

    def test_revoked_refresh_token_raises(self):
        xai_oauth.save_credential({"access": "old", "refresh": "dead", "expires": 1})
        with (
            patch("httpx.post", return_value=_resp(400, {"error": "invalid_grant"})),
            pytest.raises(LLMError, match="invalid_grant"),
        ):
            xai_oauth.get_access_token()


class TestKeyResolution:
    def _login(self, access="oauth-token"):
        future = int(time.time() * 1000) + 600_000
        xai_oauth.save_credential({"access": access, "refresh": "r", "expires": future})

    def _key(self, config: LLMConfig) -> str:
        provider = ResponsesProvider(config)
        return provider._build_headers()["Authorization"]

    def test_uses_oauth_token_when_logged_in(self):
        self._login()
        assert self._key(_xai_config()) == "Bearer oauth-token"

    def test_xai_api_key_env_wins_over_login(self, monkeypatch):
        self._login()
        monkeypatch.setenv("XAI_API_KEY", "xai-key")
        assert self._key(_xai_config()) == "Bearer xai-key"

    def test_other_services_keys_are_never_sent_to_xai(self, monkeypatch):
        """api_key_env left at the OpenRouter default must not leak that key."""
        self._login()
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-secret")
        assert self._key(_xai_config()) == "Bearer oauth-token"

    def test_without_login_or_key_fails_clearly(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
        with pytest.raises(LLMError, match="mira login xai"):
            self._key(_xai_config())

    def test_empty_api_key_env_still_means_no_auth(self):
        config = _xai_config(api_key_env="")
        assert _get_api_key(config, ResponsesProvider(config).profile) == ""

    def test_headers_pick_up_refreshed_token(self):
        self._login("first")
        provider = ResponsesProvider(_xai_config())
        assert provider._build_headers()["Authorization"] == "Bearer first"
        self._login("second")
        assert provider._build_headers()["Authorization"] == "Bearer second"

    def test_non_xai_profiles_still_cache_headers(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "k1")
        provider = ResponsesProvider(
            LLMConfig(model="m", base_url="https://openrouter.ai/api/v1", api_style="responses")
        )
        assert provider._build_headers()["Authorization"] == "Bearer k1"
        monkeypatch.setenv("OPENROUTER_API_KEY", "k2")
        assert provider._build_headers()["Authorization"] == "Bearer k1"

    def test_reasoning_effort_max_maps_to_high(self):
        provider = ResponsesProvider(_xai_config(reasoning_effort="max"))
        body: dict = {"model": "grok-4.5"}
        provider._apply_reasoning(body)
        assert body["reasoning"] == {"effort": "high"}


class TestCli:
    def test_login_saves_credential(self, auth_path):
        responses = [_resp(200, DEVICE_BODY), _resp(200, TOKEN_BODY)]
        with (
            patch("httpx.post", side_effect=responses),
            patch("time.sleep"),
            patch("webbrowser.open") as browser,
        ):
            result = CliRunner().invoke(main, ["login", "xai"])
        assert result.exit_code == 0, result.output
        assert "ABCD-EFGH" in result.output
        browser.assert_called_once()
        assert xai_oauth.load_credential()["access"] == "acc-1"

    def test_login_no_browser(self):
        responses = [_resp(200, DEVICE_BODY), _resp(200, TOKEN_BODY)]
        with (
            patch("httpx.post", side_effect=responses),
            patch("time.sleep"),
            patch("webbrowser.open") as browser,
        ):
            result = CliRunner().invoke(main, ["login", "xai", "--no-browser"])
        assert result.exit_code == 0
        browser.assert_not_called()

    def test_login_denied_exits_nonzero(self):
        responses = [_resp(200, DEVICE_BODY), _resp(400, {"error": "access_denied"})]
        with patch("httpx.post", side_effect=responses), patch("time.sleep"):
            result = CliRunner().invoke(main, ["login", "xai", "--no-browser"])
        assert result.exit_code != 0
        assert "denied" in result.output
        assert xai_oauth.load_credential() is None

    def test_logout(self):
        xai_oauth.save_credential({"access": "a", "refresh": "r", "expires": 1})
        result = CliRunner().invoke(main, ["logout", "xai"])
        assert result.exit_code == 0
        assert xai_oauth.load_credential() is None
        assert "No stored" in CliRunner().invoke(main, ["logout", "xai"]).output
