"""Credential resolution and storage. See docs/PR0_Feasibility.md section 3.1 for the rules."""
from __future__ import annotations

import base64
import os
import re
import stat
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ..config import atomic_write, render_toml
from .errors import CredentialsMissing

SECTIONS = {"linear": "linear", "jira": "atlassian"}
_MISSING_HELP = {
    "linear": "set AE_SDLC_LINEAR_API_KEY in the project's .env (or LINEAR_API_KEY), "
              "or run ae-sdlc backend connect linear",
    "jira": "set AE_SDLC_ATLASSIAN_SERVICE_ACCOUNT_KEY, or AE_SDLC_ATLASSIAN_EMAIL and AE_SDLC_ATLASSIAN_API_TOKEN, "
            "in the project's .env (or the ATLASSIAN_* variables), or run ae-sdlc backend connect jira",
}
# The project-scoped variables: read from the process environment or from <project>/.env.
# The prefix keeps them apart from the ambient LINEAR_API_KEY a shell may carry for another workspace.
PROJECT_VARIABLES = {
    "linear": {"AE_SDLC_LINEAR_API_KEY": "api_key"},
    "jira": {"AE_SDLC_ATLASSIAN_SERVICE_ACCOUNT_KEY": "service_account_key",
             "AE_SDLC_ATLASSIAN_EMAIL": "email", "AE_SDLC_ATLASSIAN_API_TOKEN": "api_token"},
}
_PLAIN_VALUE = re.compile(r"[A-Za-z0-9_.:/+=@-]+")


@dataclass(frozen=True)
class Credential:
    authorization: str = field(repr=False)
    source: str  # "project", "env", "file" or "prompt"; the only part that may be displayed
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


def _parse_dotenv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = re.fullmatch(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*", line)
        if not match:
            continue
        name, raw = match.groups()
        if raw[:1] == '"':
            end = re.search(r'(?<!\\)"', raw[1:])
            raw = re.sub(r'\\(["\\])', r"\1", raw[1:end.start() + 1] if end else raw[1:])
        elif raw[:1] == "'":
            raw = raw[1:].split("'", 1)[0]
        else:
            raw = raw.split(" #", 1)[0].rstrip()
        values[name] = raw
    return values


def project_env_file(root: Path) -> Path:
    return root / ".env"


def _from_project(provider: str, env: Mapping[str, str], root: Path | None) -> Credential | None:
    """AE_SDLC_* credentials from the process environment, else from the project's .env.

    The .env file is only read here, never executed. Unlike the credentials file, its mode is not
    checked: .env files are shared with other tools (docker compose, the app itself) that need it as it is."""
    names = PROJECT_VARIABLES[provider]
    found = {name: env[name] for name in names if env.get(name)}
    path = project_env_file(root) if root else None
    if path is not None and len(found) < len(names):
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            pass
        except (OSError, UnicodeDecodeError) as exc:
            raise CredentialsMissing(f"cannot read {path}: {getattr(exc, 'strerror', None) or 'not valid UTF-8'}") from None
        else:
            found = {**{n: v for n, v in _parse_dotenv(text).items() if n in names and v}, **found}
    return build_credential(provider, {key: found[name] for name, key in names.items() if name in found}, "project")


def resolve_optional(provider: str, env: Mapping[str, str] = os.environ,
                     root: Path | None = None) -> Credential | None:
    """First match wins: AE_SDLC_* (process environment, then the project's .env), the ambient
    LINEAR_API_KEY / ATLASSIAN_* variables, then the credentials file."""
    if credential := _from_project(provider, env, root):
        return credential
    if credential := _from_env(provider, env):
        return credential
    section = _read_file(credentials_file(env)).get(SECTIONS[provider])
    return build_credential(provider, section, "file") if isinstance(section, dict) else None


def resolve(provider: str, env: Mapping[str, str] = os.environ, root: Path | None = None) -> Credential:
    credential = resolve_optional(provider, env, root)
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


def _dotenv_line(name: str, value: str) -> str:
    if _PLAIN_VALUE.fullmatch(value):
        return f"{name}={value}"
    return f'{name}="' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _git_ignores(root: Path, name: str) -> bool:
    ignore = root / ".gitignore"
    try:
        if name in {line.strip().lstrip("/") for line in ignore.read_text(encoding="utf-8").splitlines()}:
            return True
    except (OSError, UnicodeDecodeError):
        pass
    try:
        return subprocess.run(["git", "check-ignore", "-q", name], cwd=root, capture_output=True).returncode == 0
    except OSError:
        return False


def save_project(root: Path, provider: str, values: dict[str, str]) -> Path:
    """Write one provider's values to <project>/.env as AE_SDLC_* lines, keeping every other line and the
    file's existing mode (a new file gets 600), and make sure git ignores the file."""
    path = project_env_file(root)
    mode = 0o600
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        lines = []
    except (OSError, UnicodeDecodeError) as exc:
        raise CredentialsMissing(f"cannot update {path}: {getattr(exc, 'strerror', None) or 'not valid UTF-8'}") from None
    names = {key: name for name, key in PROJECT_VARIABLES[provider].items()}
    # A key left from another credential form (a service-account key next to an email and token) would win; drop them all.
    owned = re.compile(r"\s*(?:export\s+)?(" + "|".join(map(re.escape, names.values())) + r")\s*=")
    lines = [line for line in lines if not owned.match(line)]
    lines += [_dotenv_line(names[key], value) for key, value in values.items() if value]
    atomic_write(path, "\n".join(lines) + "\n", mode)
    if not _git_ignores(root, ".env"):
        ignore = root / ".gitignore"
        existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
        ignore.write_text(existing + ("" if not existing or existing.endswith("\n") else "\n") + ".env\n", encoding="utf-8")
    return path
