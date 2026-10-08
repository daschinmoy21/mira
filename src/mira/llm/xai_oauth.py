"""xAI (Grok) subscription login — OAuth device-code flow (RFC 8628).

Lets a SuperGrok / X Premium account stand in for an ``XAI_API_KEY``: run
``mira login xai`` once, approve the code in a browser, and Mira calls
``https://api.x.ai/v1`` with the resulting bearer token, refreshing it as it
expires. Mirrors the flow used by the Grok Build CLI and the ``pi`` harness.

The credential is stored as JSON (``access`` / ``refresh`` / ``expires`` in ms
epoch) at ``$MIRA_XAI_AUTH_FILE``, else ``$MIRA_INDEX_DIR/xai-oauth.json``, else
``~/.mira/xai-oauth.json``. In Docker, keep it on the persistent data volume.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from mira.exceptions import LLMError

logger = logging.getLogger(__name__)

CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
SCOPE = "openid profile email offline_access grok-cli:access api:access"
DEVICE_CODE_URL = "https://auth.x.ai/oauth2/device/code"
TOKEN_URL = "https://auth.x.ai/oauth2/token"
_REFERRER = "mira"
_HTTP_TIMEOUT = 15
# Refresh slightly early so a token never dies mid-request.
_REFRESH_SKEW_MS = 5 * 60 * 1000
_DEFAULT_LIFETIME_S = 3600
_DEFAULT_POLL_INTERVAL_S = 5

# Serializes refreshes: xAI rotates refresh tokens, so two concurrent refreshes
# with the same token would leave one caller holding a dead credential.
_lock = threading.Lock()


class XaiLoginError(Exception):
    """Login / refresh failed (denied, expired, malformed response, ...)."""


@dataclass(frozen=True)
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    interval: int
    expires_in: int


# ── Credential storage ──────────────────────────────────────────────


def auth_file() -> Path:
    explicit = os.environ.get("MIRA_XAI_AUTH_FILE")
    if explicit:
        return Path(explicit)
    index_dir = os.environ.get("MIRA_INDEX_DIR")
    base = Path(index_dir) if index_dir else Path.home() / ".mira"
    return base / "xai-oauth.json"


def load_credential() -> dict | None:
    try:
        data = json.loads(auth_file().read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("access") or not data.get("refresh"):
        return None
    return data


def save_credential(credential: dict) -> None:
    path = auth_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-rename so a crash never leaves a truncated credential; the
    # temp file is created 0600 so the token is never world-readable.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".xai-oauth-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(credential, f)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def clear_credential() -> bool:
    """Delete the stored credential. Returns True if one existed."""
    try:
        auth_file().unlink()
        return True
    except FileNotFoundError:
        return False


# ── Protocol ────────────────────────────────────────────────────────


def _post_form(url: str, fields: dict[str, str]) -> tuple[int, dict]:
    resp = httpx.post(
        url,
        data=fields,
        headers={"Accept": "application/json"},
        timeout=_HTTP_TIMEOUT,
    )
    try:
        body = resp.json()
    except ValueError:
        raise XaiLoginError(f"xAI returned invalid JSON (HTTP {resp.status_code})") from None
    return resp.status_code, body if isinstance(body, dict) else {}


def _failure(action: str, status: int, body: dict) -> XaiLoginError:
    detail = ": ".join(str(body[k]) for k in ("error", "error_description") if body.get(k))
    return XaiLoginError(f"xAI {action} failed (HTTP {status})" + (f": {detail}" if detail else ""))


def _require_https(raw: str) -> str:
    # The URL is opened in a browser; refuse anything but https.
    if urlparse(raw).scheme != "https":
        raise XaiLoginError("xAI returned an untrusted verification URL")
    return raw


def _credential_from_token_response(body: dict, previous_refresh: str | None = None) -> dict:
    access = body.get("access_token")
    refresh = body.get("refresh_token") or previous_refresh
    if not access or not refresh:
        raise XaiLoginError("xAI token response is missing access/refresh token")
    lifetime = body.get("expires_in")
    if not isinstance(lifetime, int | float) or lifetime <= 0:
        lifetime = _DEFAULT_LIFETIME_S
    return {
        "type": "oauth",
        "access": access,
        "refresh": refresh,
        "expires": int(time.time() * 1000 + lifetime * 1000 - _REFRESH_SKEW_MS),
    }


def request_device_code() -> DeviceCode:
    status, body = _post_form(
        DEVICE_CODE_URL, {"client_id": CLIENT_ID, "scope": SCOPE, "referrer": _REFERRER}
    )
    if status != 200:
        raise _failure("device authorization", status, body)
    try:
        complete = body.get("verification_uri_complete")
        interval = body.get("interval")
        return DeviceCode(
            device_code=str(body["device_code"]),
            user_code=str(body["user_code"]),
            verification_uri=_require_https(str(body["verification_uri"])),
            verification_uri_complete=_require_https(complete) if complete else None,
            interval=int(interval) if isinstance(interval, int | float) and interval > 0 else 0,
            expires_in=int(body["expires_in"]),
        )
    except (KeyError, TypeError, ValueError):
        raise XaiLoginError("xAI device authorization response was malformed") from None


def poll_for_tokens(
    device: DeviceCode,
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict:
    """Block until the user approves the device code; return the credential."""
    interval = device.interval or _DEFAULT_POLL_INTERVAL_S
    deadline = monotonic() + device.expires_in
    while monotonic() < deadline:
        sleep(interval)
        status, body = _post_form(
            TOKEN_URL,
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": CLIENT_ID,
                "device_code": device.device_code,
            },
        )
        if status == 200:
            return _credential_from_token_response(body)
        error = body.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval = max(interval + 5, int(body.get("interval") or 0))
            continue
        if error in ("access_denied", "authorization_denied"):
            raise XaiLoginError("xAI device authorization was denied")
        if error == "expired_token":
            break
        raise _failure("device token polling", status, body)
    raise XaiLoginError("xAI device code expired before it was approved")


def refresh_credential(credential: dict) -> dict:
    status, body = _post_form(
        TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": credential["refresh"],
        },
    )
    if status != 200:
        raise _failure("token refresh", status, body)
    return _credential_from_token_response(body, previous_refresh=credential["refresh"])


# ── Runtime access ──────────────────────────────────────────────────


def stored_login_exists() -> bool:
    return load_credential() is not None


def get_access_token() -> str:
    """Return a valid access token, refreshing (and persisting) if expired.

    Raises ``LLMError("xai_login_required")`` when no login is stored or the
    refresh token has been revoked/expired.
    """
    with _lock:
        credential = load_credential()
        if credential is None:
            raise LLMError("xai_login_required", reason="no stored login")
        if time.time() * 1000 < credential.get("expires", 0):
            return credential["access"]
        try:
            credential = refresh_credential(credential)
        except (XaiLoginError, httpx.HTTPError) as exc:
            raise LLMError("xai_login_required", reason=str(exc)) from exc
        save_credential(credential)
        logger.info("Refreshed xAI OAuth token")
        return credential["access"]
