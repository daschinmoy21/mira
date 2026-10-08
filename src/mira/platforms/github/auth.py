"""GitHub authentication: App JWT + installation tokens, or a static PAT.

``GitHubAppAuth`` is the upstream installation model. ``GitHubTokenAuth`` is the
personal-deploy path — a long-lived PAT for a collaborator user account,
matching GitLabTokenAuth / ForgejoTokenAuth.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Union

import httpx
import jwt

from mira.exceptions import WebhookError

logger = logging.getLogger(__name__)

# Tokens last 60 min; refresh when less than 5 min remaining.
_TOKEN_TTL = 55 * 60  # 55 minutes
_TOKEN_MIN_REMAINING = 5 * 60  # 5 minutes

# GitHub Enterprise Server support — override via MIRA_GITHUB_API_URL
# (e.g. "https://github.acme-corp.com/api/v3").
_GITHUB_API_URL = os.environ.get(
    "MIRA_GITHUB_API_URL",
    "https://api.github.com",
).rstrip("/")


def _resolve_private_key(value: str) -> str:
    """Accept either raw PEM text or `@path/to/key.pem` and return PEM text."""
    if value.startswith("@"):
        with open(value[1:]) as f:
            return f.read()
    return value


def _github_api_url() -> str:
    return os.environ.get("MIRA_GITHUB_API_URL", "https://api.github.com").rstrip("/")


class GitHubTokenAuth:
    """Static personal access token. No minting, no expiry handling.

    Implements the same surface handlers expect from ``GitHubAppAuth``:
    ``get_token`` / ``get_installation_token`` (shim — always the PAT),
    ``get_bot_identity`` (``GET /user`` → login), and repo listing for dashboard
    discovery without App installations.
    """

    def __init__(self, token: str, api_url: str | None = None) -> None:
        self._token = token
        self._api_url = (api_url or _github_api_url()).rstrip("/")
        self._username_fetched = False
        self._username: str | None = None

    async def get_token(self, scope: str | int | None = None) -> str:
        return self._token

    async def get_installation_token(self, installation_id: int) -> str:
        """Compat shim — webhook handlers always call this; ignore installation_id."""
        return self._token

    async def get_bot_identity(self) -> str | None:
        """The PAT user's login via ``GET /user`` (cached)."""
        if self._username_fetched:
            return self._username
        self._username_fetched = True
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self._api_url}/user", headers=headers, timeout=10.0)
                if resp.status_code == 200:
                    login = resp.json().get("login")
                    self._username = login if isinstance(login, str) and login else None
                else:
                    logger.warning(
                        "Failed to resolve GitHub PAT identity (HTTP %d): %s",
                        resp.status_code,
                        resp.text[:200],
                    )
                    self._username = None
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Failed to resolve GitHub PAT identity: %s", exc)
            self._username = None
        return self._username

    async def list_installations(self) -> list[dict[str, object]]:
        """No installations in PAT mode — discovery uses ``list_user_repos``."""
        return []

    async def list_installation_repos(self, installation_id: int) -> list[dict[str, object]]:
        """Ignore installation id; list repos the PAT can access."""
        return await self.list_user_repos()

    async def list_user_repos(self) -> list[dict[str, object]]:
        """Paginate ``GET /user/repos`` for owner/collaborator/org membership."""
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
        }
        repos: list[dict[str, object]] = []
        url: str | None = (
            f"{self._api_url}/user/repos"
            f"?per_page=100&affiliation=owner,collaborator,organization_member"
            f"&sort=updated"
        )
        async with httpx.AsyncClient() as client:
            while url:
                resp = await client.get(url, headers=headers, timeout=30.0)
                if resp.status_code != 200:
                    logger.warning(
                        "Failed to list user repos (HTTP %d): %s",
                        resp.status_code,
                        resp.text[:200],
                    )
                    break
                batch = resp.json()
                if isinstance(batch, list):
                    repos.extend(batch)
                url = _parse_next_link(resp.headers.get("link", ""))
        return repos


# Union used by webhook handlers / server wiring (App or PAT).
GitHubAuth = Union["GitHubAppAuth", GitHubTokenAuth]


class GitHubAppAuth:
    """Handles GitHub App JWT generation and installation token caching."""

    def __init__(self, app_id: str, private_key: str) -> None:
        self._app_id = app_id
        self._private_key = _resolve_private_key(private_key)
        self._token_cache: dict[int, tuple[str, float]] = {}
        self._slug_fetched = False
        self._slug: str | None = None

    async def get_token(self, scope: str | int | None = None) -> str:
        """PlatformAuth interface — GitHub mints a per-installation token."""
        if scope is None:
            raise WebhookError("GitHub requires an installation id to mint a token")
        return await self.get_installation_token(int(scope))

    async def get_bot_identity(self) -> str | None:
        """PlatformAuth interface — the App's `@mention` slug (cached).

        Resilient: any failure (bad key, network) caches ``None`` so callers
        that use it inline (e.g. mention matching) degrade to the configured
        bot name rather than erroring.
        """
        if not self._slug_fetched:
            try:
                self._slug = await self.get_app_slug()
            except Exception as exc:
                logger.warning("Failed to resolve bot identity: %s", exc)
                self._slug = None
            self._slug_fetched = True
        return self._slug

    def _generate_jwt(self) -> str:
        """Generate an RS256-signed JWT for GitHub App authentication."""
        now = int(time.time())
        payload = {
            "iat": now - 60,  # issued-at with clock drift buffer
            "exp": now + 600,  # 10 minute expiry
            "iss": self._app_id,
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    async def get_installation_token(self, installation_id: int) -> str:
        """Get an installation access token, using cache when possible."""
        cached = self._token_cache.get(installation_id)
        if cached:
            token, expires_at = cached
            if expires_at - time.time() > _TOKEN_MIN_REMAINING:
                return token

        app_jwt = self._generate_jwt()
        url = f"{_GITHUB_API_URL}/app/installations/{installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers)
            if resp.status_code != 201:
                raise WebhookError(
                    f"Failed to get installation token (HTTP {resp.status_code}): {resp.text}"
                )
            data = resp.json()

        new_token: str = data["token"]
        new_expires_at = time.time() + _TOKEN_TTL
        self._token_cache[installation_id] = (new_token, new_expires_at)
        logger.debug("Cached installation token for %d", installation_id)
        return new_token

    async def get_app_slug(self) -> str | None:
        """Fetch this GitHub App's own slug — the `@mention` handle users type.

        Calls `GET /app`, authed with the JWT we already generate for
        installation-token requests. The slug is fixed for the lifetime of
        the App, so callers should cache the result. Returns ``None`` if
        the call fails so callers can fall back to a configured default.
        """
        app_jwt = self._generate_jwt()
        url = f"{_GITHUB_API_URL}/app"
        headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers, timeout=10.0)
                if resp.status_code != 200:
                    logger.warning(
                        "Failed to fetch app slug (HTTP %d): %s",
                        resp.status_code,
                        resp.text,
                    )
                    return None
                slug = resp.json().get("slug")
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Failed to fetch app slug: %s", exc)
            return None
        return slug if isinstance(slug, str) and slug else None

    async def list_installations(self) -> list[dict[str, object]]:
        """List all installations for this GitHub App."""
        app_jwt = self._generate_jwt()
        headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
        }
        installations: list[dict[str, object]] = []
        url: str | None = f"{_GITHUB_API_URL}/app/installations?per_page=100"

        async with httpx.AsyncClient() as client:
            while url:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    logger.warning(
                        "Failed to list installations (HTTP %d): %s",
                        resp.status_code,
                        resp.text,
                    )
                    break
                installations.extend(resp.json())
                # Follow pagination Link header
                url = _parse_next_link(resp.headers.get("link", ""))

        return installations

    async def list_installation_repos(self, installation_id: int) -> list[dict[str, object]]:
        """List all repos accessible to an installation."""
        token = await self.get_installation_token(installation_id)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }
        repos: list[dict[str, object]] = []
        url: str | None = f"{_GITHUB_API_URL}/installation/repositories?per_page=100"

        async with httpx.AsyncClient() as client:
            while url:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    logger.warning(
                        "Failed to list repos for installation %d (HTTP %d)",
                        installation_id,
                        resp.status_code,
                    )
                    break
                data = resp.json()
                repos.extend(data.get("repositories", []))
                url = _parse_next_link(resp.headers.get("link", ""))

        return repos


def _parse_next_link(link_header: str) -> str | None:
    """Extract the 'next' URL from a GitHub Link header."""
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip().strip("<>")
    return None
