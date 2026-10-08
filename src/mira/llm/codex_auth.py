"""Codex CLI account login and model catalog helpers.

Mira's Codex provider (``codex_cli.py``) runs ``codex exec`` against an
``auth.json`` created by ``codex login``. This module lets the dashboard create
that file (``codex login --device-auth``, driven as a subprocess so Codex owns
the token format and refresh), tells whether a login exists, and lists the
models the installed Codex CLI offers (``codex debug models``).

The Codex home used for dashboard logins is, in order: ``llm.codex_home`` from
config, ``$CODEX_HOME``, ``$MIRA_CODEX_HOME``, ``$MIRA_INDEX_DIR/codex``,
``~/.mira/codex``. The provider reads from the same place.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from mira.config import LLMConfig

logger = logging.getLogger(__name__)

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_URL = re.compile(r"https://[^\s\x1b]+")
_CODE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4,8}\b")
# Pass-through for a login/model-listing child that must reach the network but
# must not inherit Mira's service credentials.
_ENV_KEYS = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "NODE_EXTRA_CA_CERTS",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)
_PROMPT_TIMEOUT = 30  # seconds for Codex to print the link + code
_LOGIN_TIMEOUT = 15 * 60  # Codex codes expire after 15 minutes
_CATALOG_TIMEOUT = 30


class CodexLoginError(Exception):
    """Starting or completing the Codex device login failed."""


@dataclass(frozen=True)
class CodexDeviceCode:
    user_code: str
    verification_url: str
    expires_in: int = _LOGIN_TIMEOUT


# ── Locations ───────────────────────────────────────────────────────


def default_home() -> Path:
    explicit = os.environ.get("MIRA_CODEX_HOME")
    if explicit:
        return Path(explicit)
    index_dir = os.environ.get("MIRA_INDEX_DIR")
    base = Path(index_dir) if index_dir else Path.home() / ".mira"
    return base / "codex"


def login_home(config: LLMConfig) -> Path:
    """Where a dashboard login should write ``auth.json``."""
    explicit = config.codex_home or os.environ.get("CODEX_HOME")
    return Path(explicit).expanduser() if explicit else default_home()


def resolve_home(config: LLMConfig) -> Path | None:
    """Codex home the provider should read ``auth.json`` from, or None.

    Explicit config/env always wins (even if the file is missing, so the
    provider reports the missing file). The Mira-managed default is used only
    once a login exists there.
    """
    explicit = config.codex_home or os.environ.get("CODEX_HOME")
    if explicit:
        return Path(explicit).expanduser()
    home = default_home()
    return home if is_logged_in(home) else None


def is_logged_in(home: Path) -> bool:
    try:
        data = json.loads((home / "auth.json").read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and bool(data)


def command_path(config: LLMConfig) -> str | None:
    """Absolute path of the configured Codex executable, or None if not installed."""
    return shutil.which(config.codex_command or "codex")


def _env(home: Path) -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_KEYS if k in os.environ}
    env["CODEX_HOME"] = str(home)
    env["HOME"] = str(home)
    return env


def logout(config: LLMConfig) -> bool:
    """Remove the stored login. Returns True if one existed."""
    home = login_home(config)
    existed = is_logged_in(home)
    (home / "auth.json").unlink(missing_ok=True)
    (home / "models_cache.json").unlink(missing_ok=True)
    return existed


# ── Model catalog ───────────────────────────────────────────────────


async def fetch_models(config: LLMConfig) -> list[dict]:
    """Models the installed Codex CLI offers, via ``codex debug models``.

    Returns ``[{value, label, description, context_window, reasoning}]`` for the
    entries Codex lists publicly. Works without a login (the catalog ships with
    the CLI), so the picker is useful before the user signs in.
    """
    command = command_path(config)
    if command is None:
        raise CodexLoginError("Codex CLI not found")
    home = login_home(config)
    home.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        command,
        "debug",
        "models",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=_env(home),
        cwd=str(home),
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_CATALOG_TIMEOUT)
    except BaseException:
        with suppress(ProcessLookupError):
            proc.kill()
        raise
    if proc.returncode != 0:
        raise CodexLoginError(f"codex debug models exited with {proc.returncode}")
    return parse_model_catalog(stdout.decode("utf-8", errors="replace"))


def parse_model_catalog(raw: str) -> list[dict]:
    try:
        models = json.loads(raw).get("models") or []
    except (json.JSONDecodeError, AttributeError) as exc:
        raise CodexLoginError("codex debug models returned invalid JSON") from exc
    out = []
    for m in models:
        if not isinstance(m, dict) or m.get("visibility") != "list" or not m.get("slug"):
            continue
        levels = [
            lv["effort"]
            for lv in (m.get("supported_reasoning_levels") or [])
            if isinstance(lv, dict) and lv.get("effort")
        ]
        item: dict = {
            "value": str(m["slug"]),
            "label": str(m.get("display_name") or m["slug"]),
            "reasoning": bool(levels),
        }
        if m.get("description"):
            item["description"] = str(m["description"])
        if isinstance(m.get("context_window"), int):
            item["context_window"] = m["context_window"]
        out.append(item)
    return out


# ── Device login ────────────────────────────────────────────────────


def _clean(text: str) -> str:
    return _ANSI.sub("", text)


def parse_device_prompt(text: str) -> CodexDeviceCode | None:
    """Pull the verification link and one-time code out of Codex's output."""
    text = _clean(text)
    url = _URL.search(text)
    if url is None:
        return None
    # Search for the code after the link so a code-shaped token in the banner
    # (version strings, etc.) can't be mistaken for it.
    code = _CODE.search(text, url.end())
    if code is None:
        return None
    return CodexDeviceCode(user_code=code.group(0), verification_url=url.group(0).rstrip(".,)"))


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        with suppress(ProcessLookupError):
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        with suppress(Exception):
            await proc.wait()


async def start_device_login(
    config: LLMConfig,
) -> tuple[CodexDeviceCode, asyncio.subprocess.Process, list[str]]:
    """Launch ``codex login --device-auth`` and wait for its link + code.

    Returns the parsed prompt, the still-running process, and a live list that
    keeps collecting the process's (ANSI-stripped) output lines for diagnostics.
    """
    command = command_path(config)
    if command is None:
        raise CodexLoginError(
            f"Codex CLI command not found: {config.codex_command!r}. "
            "Install @openai/codex or set llm.codex_command."
        )
    home = login_home(config)
    home.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        home.chmod(0o700)
    proc = await asyncio.create_subprocess_exec(
        command,
        "login",
        "--device-auth",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=_env(home),
        cwd=str(home),
        start_new_session=os.name == "posix",
    )
    assert proc.stdout is not None
    lines: list[str] = []
    buffer = ""

    async def read_until_prompt() -> CodexDeviceCode:
        nonlocal buffer
        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.readline()
            if not chunk:
                raise CodexLoginError(_failure_text(lines) or "Codex exited before showing a code")
            line = _clean(chunk.decode("utf-8", errors="replace")).rstrip()
            lines.append(line)
            buffer += line + "\n"
            if (prompt := parse_device_prompt(buffer)) is not None:
                return prompt

    try:
        prompt = await asyncio.wait_for(read_until_prompt(), timeout=_PROMPT_TIMEOUT)
    except TimeoutError:
        await _terminate(proc)
        raise CodexLoginError(
            _failure_text(lines) or "Timed out waiting for Codex to show a sign-in code"
        ) from None
    except BaseException:
        await _terminate(proc)
        raise
    return prompt, proc, lines


def _failure_text(lines: list[str]) -> str:
    useful = [ln.strip() for ln in lines if ln.strip() and not ln.startswith("WARNING")]
    return " ".join(useful[-3:])[:300]


async def wait_for_login(
    proc: asyncio.subprocess.Process, lines: list[str], config: LLMConfig
) -> None:
    """Block until the Codex login process finishes; raise if it did not succeed."""
    assert proc.stdout is not None

    async def drain() -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            lines.append(_clean(raw.decode("utf-8", errors="replace")).rstrip())
        await proc.wait()

    try:
        await asyncio.wait_for(drain(), timeout=_LOGIN_TIMEOUT)
    except TimeoutError:
        await _terminate(proc)
        raise CodexLoginError("Codex sign-in code expired before it was approved") from None
    except asyncio.CancelledError:
        await _terminate(proc)
        raise
    if proc.returncode != 0 or not is_logged_in(login_home(config)):
        raise CodexLoginError(_failure_text(lines) or "Codex login did not complete")
