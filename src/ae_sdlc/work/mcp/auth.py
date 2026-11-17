"""Credential resolution and storage. See docs/PR0_Feasibility.md section 3.1 for the rules."""
from __future__ import annotations

import base64
import os
import stat
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ..config import atomic_write, render_toml
from .errors import CredentialsMissing

SECTIONS = {"linear": "linear", "jira": "atlassian"}
_MISSING_HELP = {
    "linear": "set LINEAR_API_KEY or run ae-sdlc backend connect linear",
    "jira": "set ATLASSIAN_SERVICE_ACCOUNT_KEY, or ATLASSIAN_EMAIL and ATLASSIAN_API_TOKEN, "
            "or run ae-sdlc backend connect jira",
}


@dataclass(frozen=True)
class Credential:
    authorization: str = field(repr=False)
    source: str  # "env", "file" or "prompt"; the only part that may be displayed
    secrets: list[str] = field(repr=False, default_factory=list)  # every form to redact from messages


def credentials_file(env: Mapping[str, str] = os.environ) -> Path:
    if override := env.get("AE_SDLC_CREDENTIALS_FILE"):
        return Path(override)
    base = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else Path.home() / ".config"
    return base / "ae-sdlc" / "credentials.toml"


def build_credential(provider: str, values: Mapping[str, str], source: str) -> Credential | None:
    """Build the header from stored-form values, or return None when they are incomplete."""
    if provider == "linear":
        key = values.get("api_key")
        return Credential(f"Bearer {key}", source, [key]) if key else None
    if key := values.get("service_account_key"):
        return Credential(f"Bearer {key}", source, [key])
    email, token = values.get("email"), values.get("api_token")
    if email and token:
        encoded = base64.b64encode(f"{email}:{token}".encode()).decode()
        return Credential(f"Basic {encoded}", source, [token, encoded])
    return None


def _from_env(provider: str, env: Mapping[str, str]) -> Credential | None:
    if provider == "linear":
        values = {"api_key": env.get("LINEAR_API_KEY", "")}
    else:
        values = {"service_account_key": env.get("ATLASSIAN_SERVICE_ACCOUNT_KEY", ""),
                  "email": env.get("ATLASSIAN_EMAIL", ""), "api_token": env.get("ATLASSIAN_API_TOKEN", "")}
    return build_credential(provider, values, "env")


def _read_file(path: Path) -> dict:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise CredentialsMissing(f"cannot read {path}: {exc.strerror}") from None
    if stat.S_IMODE(mode) & 0o077:
        raise CredentialsMissing(f"{path} is accessible by other users; fix it with: chmod 600 {path}")
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CredentialsMissing(f"cannot read {path}: {exc.strerror}") from None
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        # The parser's message can quote the offending line, which may hold a secret.
        raise CredentialsMissing(f"{path} is not valid TOML") from None


def resolve_optional(provider: str, env: Mapping[str, str] = os.environ) -> Credential | None:
    if credential := _from_env(provider, env):
        return credential
    section = _read_file(credentials_file(env)).get(SECTIONS[provider])
    return build_credential(provider, section, "file") if isinstance(section, dict) else None


def resolve(provider: str, env: Mapping[str, str] = os.environ) -> Credential:
    credential = resolve_optional(provider, env)
    if credential is None:
        raise CredentialsMissing(f"no {provider} credentials found: {_MISSING_HELP[provider]}")
    return credential


def save(provider: str, values: dict[str, str], env: Mapping[str, str] = os.environ) -> Path:
    """Store one provider's values, keeping the other provider's, with mode 600 from creation on."""
    path = credentials_file(env)
    stored = {table: dict(v) for table, v in _read_file(path).items() if isinstance(v, dict)}
    stored[SECTIONS[provider]] = values
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_write(path, render_toml(stored), 0o600)
    return path
