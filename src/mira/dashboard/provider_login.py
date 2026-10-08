"""In-memory device-login sessions for the dashboard's provider login.

A login is a short-lived background job: the dashboard POSTs to start one,
shows the returned code + link, then polls :func:`snapshot` until the job
reports ``complete`` or ``failed``. One session per provider; starting a new
one cancels the previous. Sessions are process-local and never persisted — the
durable result is the credential file written by the provider's login flow.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field

import httpx

from mira.config import LLMConfig
from mira.llm import codex_auth, xai_oauth

logger = logging.getLogger(__name__)

XAI = "xai"
CODEX = "codex-cli"


@dataclass
class _Session:
    state: str = "pending"
    user_code: str | None = None
    verification_url: str | None = None
    expires_in: int | None = None
    error: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    task: asyncio.Task | None = None

    def view(self) -> dict:
        out: dict = {"state": self.state}
        if self.state == "pending":
            out.update(
                user_code=self.user_code,
                verification_url=self.verification_url,
                expires_in=self.expires_in,
            )
        if self.error:
            out["error"] = self.error
        return out


_sessions: dict[str, _Session] = {}


def snapshot(provider: str) -> dict:
    session = _sessions.get(provider)
    return session.view() if session else {"state": "idle"}


def cancel(provider: str) -> None:
    session = _sessions.pop(provider, None)
    if session is None:
        return
    session.cancel.set()
    if session.task is not None and not session.task.done():
        session.task.cancel()


def _replace(provider: str, session: _Session) -> None:
    cancel(provider)
    _sessions[provider] = session


async def start(provider: str, config: LLMConfig) -> dict:
    """Begin a device login for ``provider`` and return its pending snapshot.

    Raises ``xai_oauth.XaiLoginError`` / ``codex_auth.CodexLoginError`` /
    ``httpx.HTTPError`` if the provider can't issue a code.
    """
    if provider == XAI:
        return await _start_xai()
    if provider == CODEX:
        return await _start_codex(config)
    raise ValueError(f"unknown provider {provider!r}")


async def _start_xai() -> dict:
    device = await asyncio.to_thread(xai_oauth.request_device_code)
    session = _Session(
        user_code=device.user_code,
        verification_url=device.verification_uri_complete or device.verification_uri,
        expires_in=device.expires_in,
    )

    def wait(seconds: float) -> None:
        if session.cancel.wait(seconds):
            raise xai_oauth.XaiLoginError("Login cancelled")

    async def run() -> None:
        try:
            credential = await asyncio.to_thread(xai_oauth.poll_for_tokens, device, sleep=wait)
            xai_oauth.save_credential(credential)
            session.state = "complete"
        except (xai_oauth.XaiLoginError, httpx.HTTPError) as exc:
            session.state, session.error = "failed", str(exc)
        except Exception:
            logger.exception("xAI login failed unexpectedly")
            session.state, session.error = "failed", "Unexpected error during xAI login"

    _replace(XAI, session)
    session.task = asyncio.create_task(run())
    return session.view()


async def _start_codex(config: LLMConfig) -> dict:
    prompt, proc, lines = await codex_auth.start_device_login(config)
    session = _Session(
        user_code=prompt.user_code,
        verification_url=prompt.verification_url,
        expires_in=prompt.expires_in,
    )

    async def run() -> None:
        try:
            await codex_auth.wait_for_login(proc, lines, config)
            session.state = "complete"
        except codex_auth.CodexLoginError as exc:
            session.state, session.error = "failed", str(exc)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Codex login failed unexpectedly")
            session.state, session.error = "failed", "Unexpected error during Codex login"

    _replace(CODEX, session)
    session.task = asyncio.create_task(run())
    return session.view()
